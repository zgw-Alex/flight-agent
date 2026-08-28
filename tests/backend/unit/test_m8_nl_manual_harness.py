from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

HARNESS_PATH = Path(__file__).parents[3] / "scripts" / "dev" / "m8_nl_manual_harness.py"


def load_harness():
    spec = importlib.util.spec_from_file_location("m8_nl_manual_harness", HARNESS_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parser_mode_invokes_existing_parser_boundary_and_renders_resolved_output() -> None:
    harness = load_harness()

    report = harness.run_capability(
        "parser",
        "9月10日从北京去上海，预算1000元以内，必须直飞。",
        "deterministic",
    )

    assert report["capability"] == "Initial Parser"
    assert report["interpretation_status"] == "RESOLVED"
    assert report["routing"] == "deterministic"
    assert report["deepseek_called"] == "NO"
    assert report["authoritative_commit_performed"] == "NO"
    assert _contains(report["proposal_preview"], "MAX_PRICE")
    assert _contains(report["proposal_preview"], "MAX_STOPS")


def test_parser_ambiguity_is_visible_and_not_converted_to_success() -> None:
    harness = load_harness()

    report = harness.run_capability("parser", "预算一千多。", "deterministic")

    assert report["result_status"] == "UNRESOLVED"
    assert report["interpretation_status"] == "CLARIFICATION_REQUIRED"
    assert _contains(report["ambiguity_unresolved"], "MISSING")
    assert report["authoritative_commit_performed"] == "NO"


def test_deepseek_metadata_is_only_displayed_from_existing_safe_metadata() -> None:
    harness = load_harness()

    report = harness.run_capability(
        "parser",
        "9月10日从北京去上海，越便宜越好但别太早",
        "deterministic",
    )

    assert report["deepseek_called"] == "NO"
    assert report["model_identity"] == "deepseek-v4-flash"
    assert report["prompt_identity"] == "NOT AVAILABLE"
    assert report["schema_adapter_config_identity"]["adapter_version"] == "NOT AVAILABLE"


def test_secret_values_are_never_rendered(monkeypatch) -> None:
    harness = load_harness()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "super-secret-test-key")
    monkeypatch.setenv("LLM_REQUIREMENT_INTERPRETER_PROVIDER", "fake")

    report = harness.run_capability("parser", "9月10日从北京去上海", "auto")

    assert not _contains(report, "super-secret-test-key")
    assert report["runtime_mode"] == "deterministic"


def test_harness_does_not_trigger_search_or_publication() -> None:
    source = HARNESS_PATH.read_text(encoding="utf-8")

    forbidden = (
        "ExecuteReadyRequirementSearch",
        "PublishRecommendation",
        "execute_initial_requirement",
        "execute_patch_requirement",
        "commit_requirement_transition",
        "apply_patch_proposal",
    )
    assert all(token not in source for token in forbidden)


def test_patch_mode_uses_approved_current_requirement_context_without_commit() -> None:
    harness = load_harness()

    report = harness.run_capability(
        "patch",
        "直飞不用必须，最好直飞就行。",
        "deterministic",
    )

    assert report["capability"] == "Patch Understanding"
    assert report["base_requirement"]["version"] == {"value": 1}
    assert report["result_status"] == "SUCCESS"
    assert _contains(report["proposal_preview"], "REMOVE_CONSTRAINT")
    assert _contains(report["proposal_preview"], "ADD_PREFERENCE")
    assert report["authoritative_commit_performed"] == "NO"


def test_patch_ambiguity_does_not_perform_authoritative_commit() -> None:
    harness = load_harness()

    report = harness.run_capability("patch", "把那个限制删掉", "deterministic")

    assert report["result_status"] == "UNRESOLVED"
    assert report["interpretation_status"] == "CLARIFICATION_REQUIRED"
    assert _contains(report["ambiguity_unresolved"], "Ambiguous target reference")
    assert report["authoritative_commit_performed"] == "NO"


def test_no_frontend_dependency_is_introduced() -> None:
    source = HARNESS_PATH.read_text(encoding="utf-8").lower()

    assert "apps/frontend" not in source
    assert "fastapi" not in source
    assert "react" not in source


def _contains(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_contains(key, needle) or _contains(item, needle) for key, item in value.items())
    if isinstance(value, list | tuple):
        return any(_contains(item, needle) for item in value)
    return needle in str(value)
