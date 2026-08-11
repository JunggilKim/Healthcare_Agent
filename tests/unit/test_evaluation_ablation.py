import pytest
from pydantic import ValidationError

from backend.app.evaluation.ablation import ablation_config


@pytest.mark.parametrize("ablation_id", ["A1", "A2", "A3"])
def test_safety_removing_ablation_is_eval_only(ablation_id: str) -> None:
    assert ablation_config(ablation_id, app_env="eval").app_env == "eval"
    with pytest.raises(ValidationError, match="SAFETY_ABLATIONS_REQUIRE_APP_ENV_EVAL"):
        ablation_config(ablation_id, app_env="prod")


@pytest.mark.parametrize("ablation_id", ["A4", "A5", "A6", "A7", "A8"])
def test_non_safety_ablation_remains_offline_configurable(ablation_id: str) -> None:
    assert ablation_config(ablation_id, app_env="test").app_env == "test"
