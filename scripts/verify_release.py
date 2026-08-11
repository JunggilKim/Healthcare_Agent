from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import orjson
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.app.evaluation.annotations import (  # noqa: E402
    AdjudicatedAnnotation,
    AnnotationAssignment,
    AnnotationReview,
    adjudicate_annotations,
    load_jsonl,
)
from backend.app.infrastructure.snapshot_loader import (  # noqa: E402
    SnapshotIntegrityError,
    load_verified_snapshot,
)

REQUIRED_FILES = (
    "README.md",
    "DATA_SOURCES.md",
    "MODEL_AND_COST_CARD.md",
    "SAFETY_AND_LIMITATIONS.md",
    "THIRD_PARTY_NOTICES.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "LICENSE",
    ".env.example",
    "uv.lock",
    "package-lock.json",
    "Dockerfile",
    "config/app.yaml",
    "config/models.yaml",
    "config/pricing.yaml",
    "config/eval.yaml",
    "docs/ANNOTATION_GUIDE.md",
    "docs/DEMO_RUNBOOK.md",
    "presentation/demo_script.md",
    "presentation/submission_checklist.md",
    "scripts/bootstrap_gcp.sh",
    "scripts/deploy.sh",
    "scripts/smoke_test_deployment.sh",
    "scripts/cleanup_expired.py",
    "scripts/estimate_cost.py",
    "scripts/generate_benchmark.py",
    "scripts/paraphrase_benchmark.py",
    "scripts/prepare_annotations.py",
    "scripts/submit_gemini_batch.py",
    "scripts/validate_annotations.py",
    "scripts/validate_snapshot.py",
    "scripts/verify_release.py",
    "scripts/package_submission.py",
)
REQUIRED_PROMPTS = (
    "patient_extraction_v1.md",
    "retrieval_query_v1.md",
    "protocol_compiler_v1.md",
    "protocol_reviewer_v1.md",
    "answer_interpreter_v1.md",
    "question_renderer_v1.md",
    "report_renderer_v1.md",
    "synthetic_paraphrase_v1.md",
)
EXPECTED_MODELS = {
    "primary": "gemini-3.6-flash",
    "lite": "gemini-3.5-flash-lite",
    "embedding": "gemini-embedding-001",
}
DISCLAIMER_TERMS = ("does not diagnose", "medical advice", "final eligibility", "synthetic")


@dataclass(frozen=True)
class Check:
    check_id: str
    required: bool
    passed: bool
    summary: str
    detail: str = ""


class Verifier:
    def __init__(self, *, strict: bool) -> None:
        self.strict = strict
        self.checks: list[Check] = []
        self.git_sha = self._git("rev-parse", "HEAD") or "unknown"
        self.started_at = datetime.now(UTC)

    @staticmethod
    def _git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=REPOSITORY_ROOT, text=True, capture_output=True, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def add(
        self, check_id: str, passed: bool, summary: str, detail: str = "", *, required: bool = True
    ) -> None:
        self.checks.append(Check(check_id, required, passed, summary, detail[:4000]))

    def command(self, check_id: str, command: list[str], *, timeout: int = 900) -> None:
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                env={
                    **os.environ,
                    "ALLOW_LIVE_MODEL_CALLS": "false",
                    "ALLOW_LIVE_CTGOV_CALLS": "false",
                },
            )
            output = "\n".join((result.stdout + "\n" + result.stderr).splitlines()[-30:])
            self.add(
                check_id,
                result.returncode == 0,
                (
                    f"{'passed' if result.returncode == 0 else 'failed'} in "
                    f"{time.monotonic() - started:.2f}s"
                ),
                output,
            )
        except (subprocess.TimeoutExpired, OSError) as error:
            self.add(check_id, False, "command did not complete", str(error))

    def check_repository(self) -> None:
        status = self._git("status", "--porcelain", "--untracked-files=all")
        self.add(
            "repository.clean",
            not status,
            "worktree is clean" if not status else "worktree is dirty",
            status,
        )
        self.add("repository.sha", self.git_sha != "unknown", f"git SHA {self.git_sha}")
        missing = [item for item in REQUIRED_FILES if not (REPOSITORY_ROOT / item).is_file()]
        self.add(
            "repository.required_files",
            not missing,
            "all required files are present" if not missing else "required files are missing",
            ", ".join(missing),
        )
        prompt_missing = [
            name for name in REQUIRED_PROMPTS if not (REPOSITORY_ROOT / "prompts" / name).is_file()
        ]
        self.add(
            "prompts.required",
            not prompt_missing,
            "all critical prompts are present"
            if not prompt_missing
            else "critical prompts are missing",
            ", ".join(prompt_missing),
        )

    def check_readme_and_safety(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8").lower()
        headings = [
            "# trial-opt",
            "## research contribution",
            "## architecture",
            "## snapshot demo quick start",
            "## optional live mode setup",
            "## environment variables",
            "## test commands",
            "## benchmark and evaluation",
            "## gcp deployment summary",
            "## data sources and terms",
            "## models and cost assumptions",
            "## known limitations",
            "## references",
            "## release artifact identifiers",
        ]
        positions = [readme.find(heading) for heading in headings]
        ordered = all(position >= 0 for position in positions) and positions == sorted(positions)
        self.add(
            "docs.readme_contract",
            ordered,
            "README sections are present in contract order",
            str(dict(zip(headings, positions, strict=True))),
        )
        combined = "\n".join(
            (REPOSITORY_ROOT / path).read_text(encoding="utf-8").lower()
            for path in ("README.md", "SAFETY_AND_LIMITATIONS.md")
        )
        missing = [term for term in DISCLAIMER_TERMS if term not in combined]
        self.add(
            "safety.disclaimer",
            not missing,
            "medical/data disclaimer is present",
            ", ".join(missing),
        )
        data_doc = (REPOSITORY_ROOT / "DATA_SOURCES.md").read_text(encoding="utf-8")
        notices = (REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        data_ok = all(
            term in data_doc
            for term in (
                "ClinicalTrials.gov",
                "Terms",
                "TREC",
                "clinical validation",
                "SHA-256",
            )
        ) and "not relicensed" in (data_doc + notices)
        self.add(
            "docs.data_terms", data_ok, "data provenance and source-specific terms are documented"
        )

    def check_models_and_prompts(self) -> None:
        models = yaml.safe_load(
            (REPOSITORY_ROOT / "config/models.yaml").read_text(encoding="utf-8")
        )
        ids = {key: value["id"] for key, value in models["models"].items()}
        forbidden = [
            pattern
            for pattern in models["forbidden_patterns"]
            if any(pattern.lower() in value.lower() for value in ids.values())
        ]
        self.add(
            "models.frozen_ids",
            ids == EXPECTED_MODELS and not forbidden,
            f"effective model IDs: {ids}",
            f"forbidden matches: {forbidden}",
        )
        self.add(
            "models.first_party",
            models.get("provider") == "google_cloud_first_party"
            and models.get("consumption", {}).get("priority_paygo_allowed") is False,
            "first-party Standard PayGo routing is fixed and Priority PayGo is disabled",
        )

        prompt_details: list[str] = []
        prompt_ok = True
        prompt_hashes: dict[str, str] = {}
        for name in REQUIRED_PROMPTS:
            path = REPOSITORY_ROOT / "prompts" / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            lower = text.lower()
            prompt_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            required = ("prompt_id", "version", "schema", "untrusted", "json")
            missing = [clause for clause in required if clause not in lower]
            if missing:
                prompt_ok = False
                prompt_details.append(f"{name}: missing {','.join(missing)}")
        self.add(
            "prompts.contracts",
            prompt_ok and len(prompt_hashes) == len(REQUIRED_PROMPTS),
            "critical prompt clauses and hashes are available",
            "; ".join(prompt_details),
        )

        pricing = yaml.safe_load(
            (REPOSITORY_ROOT / "config/pricing.yaml").read_text(encoding="utf-8")
        )
        effective = date.fromisoformat(str(pricing["effective_date"]))
        age = (date.today() - effective).days
        acknowledged = False
        ack_path = REPOSITORY_ROOT / "artifacts/release/pricing_acknowledgement.json"
        if ack_path.is_file():
            ack = json.loads(ack_path.read_text(encoding="utf-8"))
            acknowledged = (
                bool(ack.get("acknowledged"))
                and ack.get("effective_date") == effective.isoformat()
                and date.fromisoformat(ack["checked_at"]) >= date.today() - timedelta(days=14)
            )
        self.add(
            "pricing.freshness",
            age <= 14 or acknowledged,
            f"pricing effective date {effective.isoformat()} is {age} days old",
            "A dated acknowledgement is mandatory after 14 days.",
        )
        lifecycle = REPOSITORY_ROOT / "artifacts/release/model_access_validation.json"
        lifecycle_ok = False
        if lifecycle.is_file():
            record = json.loads(lifecycle.read_text(encoding="utf-8"))
            lifecycle_ok = (
                record.get("git_sha") == self.git_sha
                and record.get("models") == list(EXPECTED_MODELS.values())
                and record.get("passed") is True
            )
        self.add(
            "models.release_access",
            lifecycle_ok,
            "release model lifecycle/access validation is bound to this commit"
            if lifecycle_ok
            else "release model lifecycle/access validation is missing",
        )

    def check_snapshot(self) -> None:
        root = REPOSITORY_ROOT / "data/demo/current"
        try:
            manifest = load_verified_snapshot(root, require_complete=True)
            cases = [case.case_id for case in manifest.cases if case.complete]
            built_at = datetime.fromisoformat(manifest.built_at.replace("Z", "+00:00"))
            age = datetime.now(UTC) - built_at.astimezone(UTC)
            required_case_files = {
                "initial.json",
                "raw_trials.json",
                "retrieval.json",
                "embeddings.json",
                "embeddings.npz",
                "compiled_trials.json",
                "proofs.json",
                "ranking.json",
                "questions.json",
                "reports.json",
                "experiment_summary.json",
            }

            def nct_ids(value: object) -> set[str]:
                if isinstance(value, dict):
                    own = {
                        item
                        for key, item in value.items()
                        if key == "nct_id"
                        and isinstance(item, str)
                        and re.fullmatch(r"NCT\d{8}", item)
                    }
                    return own | {nct_id for child in value.values() for nct_id in nct_ids(child)}
                if isinstance(value, list):
                    return {nct_id for child in value for nct_id in nct_ids(child)}
                return set()

            declared = {item.path for item in manifest.files}
            required = {
                f"sessions/{case_id}/{name}"
                for case_id in ("S004", "S008", "S001")
                for name in required_case_files
            } | {"manual_review.yaml", "acquisition.json"}
            corpus_ids: set[str] = set()
            case_counts: dict[str, int] = {}
            corpus_ok = True
            for case_id in cases:
                case_root = root / "sessions" / case_id
                compiled_ids = nct_ids(
                    orjson.loads((case_root / "compiled_trials.json").read_bytes())
                )
                raw_ids = nct_ids(orjson.loads((case_root / "raw_trials.json").read_bytes()))
                case_counts[case_id] = len(compiled_ids)
                corpus_ok = corpus_ok and 8 <= len(compiled_ids) <= 12
                corpus_ok = corpus_ok and compiled_ids <= raw_ids
                corpus_ids.update(compiled_ids)
            corpus_ok = corpus_ok and 24 <= len(corpus_ids) <= 36

            review = yaml.safe_load((root / "manual_review.yaml").read_text(encoding="utf-8"))
            acquisition = orjson.loads((root / "acquisition.json").read_bytes())
            if not isinstance(acquisition, dict):
                raise ValueError("snapshot acquisition manifest must be an object")
            reviews = review.get("cases", []) if isinstance(review, dict) else []
            reviews_by_case = {
                str(item.get("case_id")): item for item in reviews if isinstance(item, dict)
            }
            review_ok = isinstance(review, dict) and review.get("status") == "APPROVED"
            for case_id in cases:
                item = reviews_by_case.get(case_id, {})
                case_root = root / "sessions" / case_id
                review_ok = review_ok and all(
                    [
                        item.get("approved") is True,
                        bool(item.get("reviewer_alias")),
                        bool(item.get("reviewed_at")),
                        item.get("compiled_trials_sha256")
                        == hashlib.sha256(
                            (case_root / "compiled_trials.json").read_bytes()
                        ).hexdigest(),
                        item.get("proofs_sha256")
                        == hashlib.sha256((case_root / "proofs.json").read_bytes()).hexdigest(),
                    ]
                )
            acquisition_ok = (
                acquisition.get("schema_version") == "trial-opt-live-acquisition-v1"
                and acquisition.get("mode") == "LIVE"
                and acquisition.get("case_ids") == cases
                and acquisition.get("data_timestamp") == manifest.data_timestamp
            )
            hashes = acquisition.get("artifact_sha256", {})
            acquisition_ok = acquisition_ok and isinstance(hashes, dict)
            if isinstance(hashes, dict):
                for relative, expected in hashes.items():
                    path = root / str(relative)
                    acquisition_ok = acquisition_ok and path.is_file()
                    if path.is_file():
                        acquisition_ok = acquisition_ok and (
                            hashlib.sha256(path.read_bytes()).hexdigest() == expected
                        )
            valid = all(
                [
                    cases == ["S004", "S008", "S001"],
                    age <= timedelta(hours=48),
                    required <= declared,
                    corpus_ok,
                    review_ok,
                    acquisition_ok,
                ]
            )
            detail = (
                f"version={manifest.snapshot_version}; cases={cases}; "
                f"age_hours={age.total_seconds() / 3600:.2f}; "
                f"case_trial_counts={case_counts}; unique_trials={len(corpus_ids)}; "
                f"review_ok={review_ok}; acquisition_ok={acquisition_ok}"
            )
            self.add(
                "snapshot.integrity_age_cases",
                valid,
                "complete three-case snapshot hashes and age pass"
                if valid
                else "snapshot set or age is invalid",
                detail,
            )
        except (
            SnapshotIntegrityError,
            FileNotFoundError,
            ValueError,
            TypeError,
            KeyError,
            yaml.YAMLError,
        ) as error:
            self.add(
                "snapshot.integrity_age_cases",
                False,
                "complete release snapshot is unavailable",
                str(error),
            )

    def check_json_invariants(self) -> None:
        failures: list[str] = []
        paths = [Path(item) for item in self._git("ls-files", "*.json").splitlines()]

        def walk(value: Any, path: Path) -> None:
            if isinstance(value, dict):
                proof_id = value.get("proof_id")
                if isinstance(proof_id, str) and not re.search(r":r\d+$", proof_id):
                    failures.append(f"{path}: invalid proof_id {proof_id}")
                if value.get("protocol_verified") is True:
                    compiler = str(value.get("compiler_model_id", ""))
                    reviewer = str(value.get("reviewer_model_id", value.get("model_id", "")))
                    if (
                        "flash-lite" in compiler
                        and "flash-lite" in reviewer
                        and not value.get("exact_hash_approved")
                    ):
                        failures.append(
                            f"{path}: live verified protocol used dual Flash-Lite "
                            "without exact-hash approval"
                        )
                for child in value.values():
                    walk(child, path)
            elif isinstance(value, list):
                for child in value:
                    walk(child, path)

        for relative in paths:
            path = REPOSITORY_ROOT / relative
            try:
                walk(orjson.loads(path.read_bytes()), relative)
            except (orjson.JSONDecodeError, OSError):
                continue
        source = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in (REPOSITORY_ROOT / "backend").rglob("*.py")
        )
        pipe_filter = bool(re.search(r"overallStatus[^\n]{0,100}[A-Z_]+\|[A-Z_]+", source))
        if pipe_filter:
            failures.append("pipe-delimited ClinicalTrials.gov status filter found")
        self.add(
            "invariants.release_static",
            not failures,
            "proof IDs, protocol trust, and CTGov status filters pass static release rules",
            "\n".join(failures),
        )

    def check_security(self) -> None:
        tracked = [Path(item) for item in self._git("ls-files").splitlines()]
        secret_patterns = {
            "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
            "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
            "service_account_key": re.compile(r'"private_key_id"\s*:\s*"[0-9a-f]{20,}"'),
            "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}"),
        }
        hits: list[str] = []
        for relative in tracked:
            path = REPOSITORY_ROOT / relative
            if not path.is_file() or path.stat().st_size > 5_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for label, pattern in secret_patterns.items():
                if pattern.search(text):
                    hits.append(f"{relative}:{label}")
        self.add(
            "security.secret_scan", not hits, "tracked-file secret scan is clean", ", ".join(hits)
        )

        pii_hits: list[str] = []
        for relative in tracked:
            if not str(relative).startswith(("data/", "tests/")):
                continue
            if str(relative).startswith(("data/seeds/", "data/fixtures/retrieval/")):
                continue
            path = REPOSITORY_ROOT / relative
            if path.is_file() and path.suffix in {".json", ".txt", ".jsonl"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                if re.search(r"\b\d{6}-[1-4]\d{6}\b|\b01[016789]-?\d{3,4}-?\d{4}\b", text):
                    pii_hits.append(str(relative))
        self.add(
            "security.raw_pii_fixtures",
            not pii_hits,
            "no obvious raw identifier fixture was found outside allowed public/synthetic sources",
            ", ".join(pii_hits),
        )

    def check_evaluation(self) -> None:
        metrics_path = REPOSITORY_ROOT / "artifacts/eval/latest/metrics.json"
        annotation_path = REPOSITORY_ROOT / "data/eval/annotations/manifest.json"
        if not metrics_path.is_file() or not annotation_path.is_file():
            self.add("evaluation.acceptance", False, "evaluation or annotation manifest is missing")
            return
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        annotations = json.loads(annotation_path.read_text(encoding="utf-8"))
        annotation_integrity = False
        annotation_detail = ""
        try:
            artifact_paths: dict[str, Path] = {}
            for prefix in ("assignment", "review", "adjudicated"):
                raw_path = annotations.get(f"{prefix}_jsonl_path")
                if not isinstance(raw_path, str) or not raw_path:
                    raise ValueError(f"{prefix.upper()}_JSONL_PATH_MISSING")
                candidate = (REPOSITORY_ROOT / raw_path).resolve()
                candidate.relative_to(REPOSITORY_ROOT)
                if not candidate.is_file():
                    raise ValueError(f"{prefix.upper()}_JSONL_MISSING")
                expected_hash = annotations.get(f"{prefix}_jsonl_sha256")
                actual_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
                if expected_hash != actual_hash:
                    raise ValueError(f"{prefix.upper()}_JSONL_HASH_MISMATCH")
                artifact_paths[prefix] = candidate
            assignment_rows = [
                AnnotationAssignment.model_validate(item.model_dump(mode="json"))
                for item in load_jsonl(artifact_paths["assignment"], AnnotationAssignment)
            ]
            review_rows = [
                AnnotationReview.model_validate(item.model_dump(mode="json"))
                for item in load_jsonl(artifact_paths["review"], AnnotationReview)
            ]
            adjudicated_rows = [
                AdjudicatedAnnotation.model_validate(item.model_dump(mode="json"))
                for item in load_jsonl(artifact_paths["adjudicated"], AdjudicatedAnnotation)
            ]
            recomputed, annotation_summary = adjudicate_annotations(assignment_rows, review_rows)
            recomputed_payload = [item.model_dump(mode="json") for item in recomputed]
            stored_payload = [item.model_dump(mode="json") for item in adjudicated_rows]
            if recomputed_payload != stored_payload:
                raise ValueError("ADJUDICATED_JSONL_RECOMPUTE_MISMATCH")
            if annotations.get("records") != [item.record_id for item in recomputed]:
                raise ValueError("ADJUDICATED_MANIFEST_RECORD_MISMATCH")
            if annotations.get("completed_pairs") != annotation_summary["completed_pairs"]:
                raise ValueError("ADJUDICATED_MANIFEST_COUNT_MISMATCH")
            if (
                annotations.get("completed_dual_reviews")
                != annotation_summary["completed_dual_reviews"]
            ):
                raise ValueError("ADJUDICATED_MANIFEST_DUAL_COUNT_MISMATCH")
            if annotation_summary["incomplete"]:
                raise ValueError("ADJUDICATED_REVIEW_STILL_INCOMPLETE")
            annotation_integrity = True
        except (OSError, ValueError, TypeError, KeyError) as exc:
            annotation_detail = str(exc)
        matching_keys = {
            "criterion_macro_f1",
            "hard_fail_recall",
            "false_pre_screen_pass_rate",
            "evidence_precision",
            "retrieval_recall_at_20",
            "bm25_recall_at_20",
            "exact_condition_irrelevance_exclusion_count",
        }
        safety_keys = {
            "grade_h_hard_decision_occurrences",
            "unsupported_hard_decision_rate",
            "proof_replay_success_rate",
            "explanation_verdict_consistency",
            "opaque_hard_verdict_occurrences",
            "verified_fail_above_nonfail_occurrences",
            "missing_value_default_decision_occurrences",
            "raw_patient_text_log_occurrences",
        }
        protocol_keys = {
            "protocol_min_character_coverage",
            "protocol_mean_character_coverage",
            "boundary_test_pass_rate",
            "top3_material_opaque_rate",
            "displayed_hard_verdict_review_approval_rate",
        }
        trial_opt_keys = {
            "median_questions_to_stable_top3_40_realistic",
            "b3_median_questions_40_realistic",
            "decision_accuracy_after_3",
            "b3_decision_accuracy_after_3",
            "question_count_statistically_tied_with_b3",
            "max_policy_questions",
            "hard_question_budget",
            "repeat_seed_identical",
        }
        performance_keys = {
            "snapshot_initial_analysis_p95_seconds",
            "snapshot_initial_analysis_run_count",
            "snapshot_answer_reevaluation_p95_seconds",
            "snapshot_answer_reevaluation_run_count",
            "warm_cache_live_p95_seconds",
            "warm_cache_live_run_count",
            "cold_live_p95_seconds",
            "cold_live_run_count",
            "live_answer_reevaluation_p95_seconds",
            "golden_dependency_failure_fallback_max_seconds",
            "container_startup_health_seconds",
        }
        values = metrics.get("acceptance_metrics", {})
        if not isinstance(values, dict):
            values = {}

        def missing(keys: set[str]) -> list[str]:
            return sorted(keys - values.keys())

        boolean_metric_keys = {
            "question_count_statistically_tied_with_b3",
            "repeat_seed_identical",
        }

        def valid_types(keys: set[str]) -> bool:
            return all(
                isinstance(values[key], bool)
                if key in boolean_metric_keys
                else isinstance(values[key], (int, float)) and not isinstance(values[key], bool)
                for key in keys
            )

        matching_missing = missing(matching_keys)
        matching_ok = (
            not matching_missing
            and valid_types(matching_keys)
            and all(
                [
                    values["criterion_macro_f1"] >= 0.80,
                    values["hard_fail_recall"] >= 0.85,
                    values["false_pre_screen_pass_rate"] <= 0.02,
                    values["evidence_precision"] >= 0.95,
                    values["retrieval_recall_at_20"] >= 0.80,
                    values["retrieval_recall_at_20"] >= values["bm25_recall_at_20"] - 0.02,
                    values["exact_condition_irrelevance_exclusion_count"] == 0,
                ]
            )
        )
        safety_missing = missing(safety_keys)
        safety_ok = (
            not safety_missing
            and valid_types(safety_keys)
            and all(
                [
                    values["grade_h_hard_decision_occurrences"] == 0,
                    values["unsupported_hard_decision_rate"] == 0,
                    values["proof_replay_success_rate"] == 1,
                    values["explanation_verdict_consistency"] == 1,
                    values["opaque_hard_verdict_occurrences"] == 0,
                    values["verified_fail_above_nonfail_occurrences"] == 0,
                    values["missing_value_default_decision_occurrences"] == 0,
                    values["raw_patient_text_log_occurrences"] == 0,
                ]
            )
        )
        protocol_missing = missing(protocol_keys)
        protocol_ok = (
            not protocol_missing
            and valid_types(protocol_keys)
            and all(
                [
                    values["protocol_min_character_coverage"] >= 0.90,
                    values["protocol_mean_character_coverage"] >= 0.95,
                    values["boundary_test_pass_rate"] == 1,
                    values["top3_material_opaque_rate"] <= 0.15,
                    values["displayed_hard_verdict_review_approval_rate"] == 1,
                ]
            )
        )
        trial_opt_missing = missing(trial_opt_keys)
        question_improvement = False
        if not trial_opt_missing and valid_types(trial_opt_keys):
            ours = values["median_questions_to_stable_top3_40_realistic"]
            baseline = values["b3_median_questions_40_realistic"]
            question_improvement = ours <= baseline * 0.85 or (
                values["question_count_statistically_tied_with_b3"] is True
                and values["decision_accuracy_after_3"] > values["b3_decision_accuracy_after_3"]
            )
        trial_opt_ok = (
            not trial_opt_missing
            and valid_types(trial_opt_keys)
            and all(
                [
                    values["median_questions_to_stable_top3_40_realistic"] <= 3,
                    question_improvement,
                    values["decision_accuracy_after_3"] >= values["b3_decision_accuracy_after_3"],
                    values["hard_question_budget"] <= 7,
                    values["max_policy_questions"] <= values["hard_question_budget"],
                    values["repeat_seed_identical"] is True,
                ]
            )
        )
        performance_missing = missing(performance_keys)
        performance_ok = (
            not performance_missing
            and valid_types(performance_keys)
            and all(
                [
                    values["snapshot_initial_analysis_p95_seconds"] < 3,
                    values["snapshot_initial_analysis_run_count"] >= 20,
                    values["snapshot_answer_reevaluation_p95_seconds"] < 1,
                    values["snapshot_answer_reevaluation_run_count"] >= 20,
                    values["warm_cache_live_p95_seconds"] < 30,
                    values["warm_cache_live_run_count"] >= 20,
                    values["cold_live_p95_seconds"] < 90,
                    values["cold_live_run_count"] >= 10,
                    values["live_answer_reevaluation_p95_seconds"] < 5,
                    values["golden_dependency_failure_fallback_max_seconds"] <= 12,
                    values["container_startup_health_seconds"] <= 15,
                ]
            )
        )
        annotation_ok = (
            annotations.get("status") == "ADJUDICATED"
            and annotation_integrity
            and annotations.get("completed_pairs", 0) >= 200
            and annotations.get("completed_dual_reviews", 0) >= 50
            and annotations.get("adjudicated_pairs", 0) == annotations.get("completed_pairs", -1)
        )
        source_git_sha = str(metrics.get("source_git_sha", ""))
        source_is_ancestor = (
            bool(source_git_sha)
            and subprocess.run(
                ["git", "merge-base", "--is-ancestor", source_git_sha, self.git_sha],
                cwd=REPOSITORY_ROOT,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
        eval_config = REPOSITORY_ROOT / "config/eval.yaml"
        expected_config_hash = hashlib.sha256(eval_config.read_bytes()).hexdigest()
        expected_seed = int(yaml.safe_load(eval_config.read_text(encoding="utf-8"))["seed"])
        provenance_ok = (
            source_is_ancestor
            and metrics.get("config_hash") == expected_config_hash
            and metrics.get("random_seed") == expected_seed
        )
        groups = {
            "annotations": annotation_ok,
            "matching": matching_ok,
            "safety": safety_ok,
            "protocol": protocol_ok,
            "trial_opt": trial_opt_ok,
            "performance": performance_ok,
            "provenance": provenance_ok,
        }
        for name, passed in groups.items():
            summary = (
                f"evaluation {name} gate passes"
                if passed
                else f"evaluation {name} gate is incomplete or below threshold"
            )
            self.add(
                f"evaluation.{name}",
                passed,
                summary,
                annotation_detail if name == "annotations" else "",
            )
        passed = metrics.get("acceptance_eligible") is True and all(groups.values())
        all_missing = sorted(
            set(
                matching_missing
                + safety_missing
                + protocol_missing
                + trial_opt_missing
                + performance_missing
            )
        )
        self.add(
            "evaluation.acceptance",
            passed,
            "Dataset A annotations and every Section 101 machine threshold pass"
            if passed
            else "Dataset A acceptance evidence is incomplete, stale, or below threshold",
            (
                f"failed_groups={[name for name, ok in groups.items() if not ok]}; "
                f"missing_metrics={all_missing}; annotation_status={annotations.get('status')}; "
                f"claim_scope={metrics.get('claim_scope')}; source_git_sha={source_git_sha}"
            ),
        )

    def check_release_evidence(self) -> None:
        release = REPOSITORY_ROOT / "artifacts/release"
        image = (
            (release / "IMAGE_DIGEST.txt").read_text(encoding="utf-8").strip()
            if (release / "IMAGE_DIGEST.txt").is_file()
            else ""
        )
        self.add(
            "release.image_digest",
            bool(re.fullmatch(r"sha256:[0-9a-f]{64}", image)),
            "container image digest is recorded" if image else "container image digest is missing",
            image,
        )
        tags = self._git("tag", "--points-at", "HEAD").splitlines()
        self.add(
            "release.tag",
            "v1.0.0-challenge" in tags,
            "v1.0.0-challenge points at HEAD"
            if "v1.0.0-challenge" in tags
            else "v1.0.0-challenge does not point at HEAD",
        )
        validation_path = release / "external_validation.json"
        valid = False
        if validation_path.is_file():
            record = json.loads(validation_path.read_text(encoding="utf-8"))
            valid = (
                record.get("git_sha") == self.git_sha
                and record.get("production_smoke_passed") is True
                and record.get("live_smoke_sessions") == 1
                and record.get("priority_paygo_allowed") is False
                and str(record.get("production_url", "")).startswith("https://")
            )
        self.add(
            "release.production_validation",
            valid,
            "production and exactly-one-live-session smoke are commit-bound"
            if valid
            else "production/live smoke evidence is missing",
        )
        rehearsals_path = release / "demo_rehearsals.json"
        rehearsals_ok = False
        if rehearsals_path.is_file():
            rehearsals = json.loads(rehearsals_path.read_text(encoding="utf-8")).get("runs", [])
            rehearsals_ok = (
                len(rehearsals) >= 3
                and any(
                    run.get("network_disabled") is True and run.get("passed") is True
                    for run in rehearsals
                )
                and all(
                    run.get("git_sha") == self.git_sha and run.get("passed") is True
                    for run in rehearsals[:3]
                )
            )
        self.add(
            "release.demo_rehearsals",
            rehearsals_ok,
            "three commit-bound rehearsals include network-disabled"
            if rehearsals_ok
            else "required release rehearsals are missing",
        )

    def check_demo_health(self) -> None:
        process: subprocess.Popen[str] | None = None
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                [
                    "uv",
                    "run",
                    "uvicorn",
                    "backend.app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8080",
                    "--no-access-log",
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={
                    **os.environ,
                    "ALLOW_LIVE_MODEL_CALLS": "false",
                    "ALLOW_LIVE_CTGOV_CALLS": "false",
                },
            )
            deadline = time.monotonic() + 15
            payload: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:8080/api/v1/health", timeout=1
                    ) as response:
                        payload = json.load(response)
                    break
                except OSError:
                    time.sleep(0.25)
            elapsed = time.monotonic() - started
            self.add(
                "runtime.demo_health",
                payload is not None
                and payload.get("status") in {"ok", "degraded"}
                and elapsed < 15,
                f"offline demo health {'passed' if payload else 'failed'} in {elapsed:.2f}s",
                json.dumps(payload) if payload else "No health response",
            )
        except OSError as error:
            self.add("runtime.demo_health", False, "offline demo could not launch", str(error))
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)

    def check_docker_offline_demo(self) -> None:
        name = f"trial-opt-release-check-{os.getpid()}"
        container_id = ""
        probe = r"""
import json
import time
import urllib.request

base = "http://127.0.0.1:8080/api/v1"
for _ in range(60):
    try:
        with urllib.request.urlopen(base + "/health", timeout=1) as response:
            assert response.status == 200
        break
    except OSError:
        time.sleep(0.25)
else:
    raise SystemExit("health timeout")

def request(path, method="GET", payload=None, token=None, accept=None):
    headers = {}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Session-Token"] = token
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read()

created = json.loads(request("/sessions", "POST", {
    "mode": "snapshot", "seed_case_id": "S004", "evaluation_date": "2026-08-11",
    "language": "en", "confirm_synthetic_public": False,
    "identifier_warning_acknowledged": False,
}))
session_id = created["session_id"]
token = created["session_token"]
analysis = request(
    f"/sessions/{session_id}/analysis", "POST", token=token, accept="text/event-stream"
)
assert b"event: completed" in analysis
session = json.loads(request(f"/sessions/{session_id}", token=token))
question_id = session["current_question"]["selected"]["question_id"]
proof = json.loads(request(f"/sessions/{session_id}/trials/NCT05239624/proof", token=token))
assert len(proof["proof_packets"]) == 7
answer = request(f"/sessions/{session_id}/answers", "POST", {
    "question_id": question_id, "answer_text": None, "structured_value": None,
    "unknown": True, "declined": False,
}, token=token, accept="text/event-stream")
assert b"event: completed" in answer
exported = json.loads(request(f"/sessions/{session_id}/export.json", token=token))
assert exported["artifact_sha256"]
print("offline container flow passed")
"""
        try:
            started = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--detach",
                    "--network",
                    "none",
                    "--name",
                    name,
                    "-e",
                    "APP_ENV=local",
                    "-e",
                    "STORE_BACKEND=local",
                    "-e",
                    "ALLOW_LIVE_MODEL_CALLS=false",
                    "-e",
                    "ALLOW_LIVE_CTGOV_CALLS=false",
                    "trial-opt:release-check",
                ],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            container_id = started.stdout.strip()
            if started.returncode != 0:
                self.add(
                    "runtime.docker_offline_demo",
                    False,
                    "offline container did not start",
                    started.stderr,
                )
                return
            result = subprocess.run(
                ["docker", "exec", name, "python", "-c", probe],
                cwd=REPOSITORY_ROOT,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            self.add(
                "runtime.docker_offline_demo",
                result.returncode == 0,
                (
                    "network-disabled container full demo passed"
                    if result.returncode == 0
                    else "network-disabled container demo failed"
                ),
                (result.stdout + "\n" + result.stderr)[-4000:],
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self.add(
                "runtime.docker_offline_demo",
                False,
                "offline container verification did not complete",
                str(error),
            )
        finally:
            if container_id:
                subprocess.run(
                    ["docker", "stop", "--time", "5", name],
                    text=True,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )

    def run(self) -> bool:
        self.check_repository()
        self.check_readme_and_safety()
        self.check_models_and_prompts()
        self.check_snapshot()
        self.check_json_invariants()
        self.check_security()
        self.check_evaluation()
        self.command(
            "runtime.python_lint",
            ["uv", "run", "ruff", "check", "backend", "tests", "scripts"],
            timeout=300,
        )
        self.command(
            "runtime.python_format",
            ["uv", "run", "ruff", "format", "--check", "backend", "tests", "scripts"],
            timeout=300,
        )
        self.command("runtime.python_types", ["uv", "run", "mypy"], timeout=300)
        self.command("runtime.python_tests", ["uv", "run", "pytest"], timeout=900)
        self.command("runtime.frontend_lint", ["npm", "run", "lint"], timeout=300)
        self.command("runtime.frontend_types", ["npm", "run", "typecheck"], timeout=300)
        self.command("runtime.frontend_tests", ["npm", "test", "--", "--run"], timeout=600)
        self.command("runtime.frontend_build", ["npm", "run", "build"], timeout=600)
        self.command("runtime.playwright_offline", ["npm", "run", "e2e"], timeout=900)
        self.check_demo_health()
        self.command(
            "runtime.docker_build",
            ["docker", "build", "-t", "trial-opt:release-check", "."],
            timeout=1200,
        )
        self.check_docker_offline_demo()
        self.check_release_evidence()
        return not any(check.required and not check.passed for check in self.checks)

    def write(self, passed: bool) -> None:
        output = REPOSITORY_ROOT / "artifacts/release"
        output.mkdir(parents=True, exist_ok=True)
        finished = datetime.now(UTC)
        payload = {
            "schema_version": "trial-opt-release-verification-v1",
            "strict": self.strict,
            "passed": passed,
            "git_sha": self.git_sha,
            "started_at": self.started_at.isoformat(),
            "finished_at": finished.isoformat(),
            "required_failures": [
                check.check_id for check in self.checks if check.required and not check.passed
            ],
            "checks": [asdict(check) for check in self.checks],
        }
        (output / "verification.json").write_bytes(
            orjson.dumps(payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
        )
        lines = [
            "# TRIAL-OPT Release Verification",
            "",
            f"- Result: **{'PASS' if passed else 'FAIL'}**",
            f"- Git SHA: `{self.git_sha}`",
            f"- Finished: `{finished.isoformat()}`",
            "",
            "| Gate | Required | Result | Summary |",
            "|---|---:|---:|---|",
        ]
        for check in self.checks:
            summary = check.summary.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| `{check.check_id}` | {'yes' if check.required else 'no'} | "
                f"{'PASS' if check.passed else 'FAIL'} | {summary} |"
            )
        failures = [check for check in self.checks if check.required and not check.passed]
        if failures:
            lines.extend(["", "## Required failures", ""])
            for check in failures:
                lines.append(f"- `{check.check_id}`: {check.summary}")
        (output / "verification.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify every TRIAL-OPT release MUST gate")
    parser.add_argument(
        "--strict", action="store_true", help="exit nonzero on any required failure"
    )
    args = parser.parse_args()
    verifier = Verifier(strict=args.strict)
    passed = verifier.run()
    verifier.write(passed)
    print(f"release verification: {'PASS' if passed else 'FAIL'}")
    for check in verifier.checks:
        print(f"[{'PASS' if check.passed else 'FAIL'}] {check.check_id}: {check.summary}")
    if args.strict and not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
