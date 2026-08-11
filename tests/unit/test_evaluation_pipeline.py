from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.evaluation.metrics import classification_metrics, retrieval_metrics
from backend.app.evaluation.worlds import generate_fixture_benchmark


def test_fixture_benchmark_is_deterministic_and_truthfully_scoped() -> None:
    fixture = load_vertical_slice()
    first = generate_fixture_benchmark(fixture, 20260811)
    second = generate_fixture_benchmark(fixture, 20260811)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.scope_status == "PROVISIONAL_FIXTURE_SMOKE"
    assert first.acceptance_eligible is False
    assert first.counts == {
        "trials": 1,
        "worlds": 9,
        "observations": 54,
        "criterion_labels": 63,
        "manual_reviews": 0,
        "dual_reviews": 0,
    }
    assert {item.pattern for item in first.observations} == {"MCAR", "REALISTIC"}
    assert {item.rate for item in first.observations} == {0.2, 0.4, 0.6}
    assert all("NCT" not in world.narrative for world in first.worlds)
    assert all("eligible" not in world.narrative.casefold() for world in first.worlds)


def test_metric_implementations_cover_required_shapes() -> None:
    classification = classification_metrics(
        ["PASS", "FAIL", "UNKNOWN"],
        ["PASS", "FAIL", "PASS"],
        ["PASS", "FAIL", "UNKNOWN"],
    )
    assert classification["accuracy"] == 2 / 3
    retrieval = retrieval_metrics(["a", "b", "c"], {"b": 2})
    assert retrieval["recall_at_20"] == 1.0
    assert retrieval["mrr"] == 0.5
