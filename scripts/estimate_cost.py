from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Estimate one bounded model session from pricing")
    parser.add_argument("--pricing", type=Path, default=Path("config/pricing.yaml"))
    parser.add_argument("--primary-input", type=int, default=0)
    parser.add_argument("--primary-output", type=int, default=0)
    parser.add_argument("--lite-input", type=int, default=0)
    parser.add_argument("--lite-output", type=int, default=0)
    parser.add_argument("--embedding-input", type=int, default=0)
    parser.add_argument("--session-cap", type=Decimal, default=Decimal("1.25"))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def nonnegative(value: int, name: str) -> Decimal:
    if value < 0:
        raise SystemExit(f"{name} must be nonnegative")
    return Decimal(value)


def main() -> None:
    args = arguments()
    pricing = yaml.safe_load(args.pricing.read_text(encoding="utf-8"))
    rates = pricing["standard_paygo_global_per_million_tokens"]
    primary = rates["gemini-3.6-flash"]
    lite = rates["gemini-3.5-flash-lite"]
    embedding = pricing["embedding"]["gemini-embedding-001"]
    million = Decimal(1_000_000)
    thousand = Decimal(1_000)
    components = {
        "primary_input": nonnegative(args.primary_input, "primary input")
        * Decimal(str(primary["input"]))
        / million,
        "primary_output_reasoning": nonnegative(args.primary_output, "primary output")
        * Decimal(str(primary["output_reasoning"]))
        / million,
        "lite_input": nonnegative(args.lite_input, "lite input")
        * Decimal(str(lite["input"]))
        / million,
        "lite_output_reasoning": nonnegative(args.lite_output, "lite output")
        * Decimal(str(lite["output_reasoning"]))
        / million,
        "embedding_input": nonnegative(args.embedding_input, "embedding input")
        * Decimal(str(embedding["online_per_1000_input_tokens"]))
        / thousand,
    }
    total = sum(components.values(), Decimal(0))
    effective = date.fromisoformat(str(pricing["effective_date"]))
    result = {
        "effective_date": effective.isoformat(),
        "age_days": (date.today() - effective).days,
        "source": pricing["source"],
        "components_usd": {
            key: str(value.quantize(Decimal("0.000001"))) for key, value in components.items()
        },
        "estimated_total_usd": str(total.quantize(Decimal("0.000001"))),
        "session_cap_usd": str(args.session_cap),
        "within_cap": total <= args.session_cap,
        "warning": "planning estimate only; verify current first-party Google Cloud pricing",
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"estimated_total_usd={result['estimated_total_usd']}")
        print(f"within_cap={result['within_cap']}; pricing_age_days={result['age_days']}")
        print(result["warning"])
    if total > args.session_cap:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
