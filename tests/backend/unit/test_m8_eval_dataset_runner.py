from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_MODULE_PATH = REPO_ROOT / "evals" / "m8" / "deepseek_eval.py"

spec = importlib.util.spec_from_file_location("m8_deepseek_eval", EVAL_MODULE_PATH)
assert spec is not None
eval_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["m8_deepseek_eval"] = eval_module
spec.loader.exec_module(eval_module)

dataset = eval_module.dataset
run_offline_catalog_check = eval_module.run_offline_catalog_check
screening_cases = eval_module.screening_cases


def test_m8_u6_dataset_has_required_capability_scale_and_classes() -> None:
    check = run_offline_catalog_check()
    counts = check["counts"]

    assert counts["parser"] == 30
    assert counts["patch"] == 35
    assert counts["explanation"] == 25
    assert counts["development"] > 0
    assert counts["regression"] > 0
    assert counts["challenge"] > 0
    assert counts["p0"] > 0
    assert check["case_ids_unique"] is True
    assert check["llm_as_judge_used_for_p0"] is False


def test_m8_u6_cases_carry_contract_fields_and_repeat_requirements() -> None:
    cases = dataset()

    assert all(case.case_id.startswith("m8-u6-") for case in cases)
    assert all(case.capability in {"parser", "patch", "explanation"} for case in cases)
    assert all(case.dataset_class in {"development", "regression", "challenge"} for case in cases)
    assert all(case.severity in {"P0", "P1", "P2"} for case in cases)
    assert all(case.input_text for case in cases)
    assert all(case.expected_status for case in cases)
    assert any(case.expected_status == "NON_COMMIT_READY" for case in cases)
    assert all(case.tags for case in cases)
    assert all(case.repeat_requirement == 3 for case in cases if case.severity == "P0")
    assert all(case.repeat_requirement == 1 for case in cases if case.severity != "P0")


def test_m8_u6_screening_matrix_keeps_capability_balance_and_early_elimination_scope() -> None:
    cases = screening_cases()

    assert sum(1 for case in cases if case.capability == "parser") == 9
    assert sum(1 for case in cases if case.capability == "patch") == 9
    assert sum(1 for case in cases if case.capability == "explanation") == 6
    assert all(case.dataset_class in {"regression", "challenge"} for case in cases)
    assert any(case.severity == "P0" for case in cases)


def test_m8_u6_real_eval_tooling_is_separate_from_ordinary_ci() -> None:
    all_ci = (REPO_ROOT / "scripts" / "ci" / "all.ps1").read_text(encoding="utf-8")
    backend_ci = (REPO_ROOT / "scripts" / "ci" / "backend.ps1").read_text(encoding="utf-8")

    assert "deepseek_eval.py" not in all_ci
    assert "deepseek_eval.py" not in backend_ci
    assert "real-full" not in all_ci
    assert "real-screening" not in backend_ci
