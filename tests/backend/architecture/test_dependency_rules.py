"""Architecture dependency guard for the backend package graph."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

BACKEND_PACKAGE = "flight_agent"
REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = REPO_ROOT / "apps" / "backend" / "src" / BACKEND_PACKAGE


@dataclass(frozen=True)
class DependencyRule:
    name: str
    layer: str
    allowed_first_party_layers: frozenset[str]
    forbidden_external_roots: frozenset[str] = frozenset()
    forbidden_message: str = ""


RULES = (
    DependencyRule(
        name="domain-boundary",
        layer="domain",
        allowed_first_party_layers=frozenset({"domain"}),
        forbidden_external_roots=frozenset(
            {
                "fastapi",
                "deepseek",
                "httpx",
                "openai",
                "pydantic",
                "requests",
                "sqlalchemy",
                "uvicorn",
            }
        ),
        forbidden_message=(
            "Domain must not depend on outer layers or web, ORM, HTTP, provider, or LLM SDKs"
        ),
    ),
    DependencyRule(
        name="ports-boundary",
        layer="ports",
        allowed_first_party_layers=frozenset({"domain", "ports"}),
        forbidden_external_roots=frozenset(
            {
                "fastapi",
                "deepseek",
                "httpx",
                "openai",
                "pydantic",
                "requests",
                "sqlalchemy",
                "uvicorn",
            }
        ),
        forbidden_message="Ports must not depend on concrete adapters or infrastructure",
    ),
    DependencyRule(
        name="application-boundary",
        layer="application",
        allowed_first_party_layers=frozenset({"application", "domain", "ports"}),
        forbidden_external_roots=frozenset(
            {
                "fastapi",
                "deepseek",
                "httpx",
                "openai",
                "pydantic",
                "requests",
                "sqlalchemy",
                "uvicorn",
            }
        ),
        forbidden_message=(
            "Application must depend only on Domain and Ports, never concrete "
            "adapters, infrastructure, ORM, or SDK implementations"
        ),
    ),
)


def test_backend_source_respects_architecture_dependency_rules() -> None:
    violations = collect_dependency_violations(SOURCE_ROOT)

    assert violations == []


def test_domain_forbidden_dependency_negative_control_fails(tmp_path: Path) -> None:
    package_root = make_package_fixture(tmp_path)
    write_module(
        package_root / "domain" / "leak.py",
        "from fastapi import APIRouter\n",
    )

    violations = collect_dependency_violations(package_root)

    assert any("domain-boundary" in violation for violation in violations)
    assert any("Domain must not depend" in violation for violation in violations)


def test_application_concrete_dependency_negative_control_fails(tmp_path: Path) -> None:
    package_root = make_package_fixture(tmp_path)
    write_module(
        package_root / "application" / "leak.py",
        "from flight_agent.adapters.fake_provider import FakeProvider\n",
    )

    violations = collect_dependency_violations(package_root)

    assert any("application-boundary" in violation for violation in violations)
    assert any(
        "Application must depend only on Domain and Ports" in violation for violation in violations
    )


def test_domain_flight_provider_port_dependency_negative_control_fails(tmp_path: Path) -> None:
    package_root = make_package_fixture(tmp_path)
    write_module(
        package_root / "domain" / "leak.py",
        "from flight_agent.ports.flight_providers import FlightProvider\n",
    )

    violations = collect_dependency_violations(package_root)

    assert any("domain-boundary" in violation for violation in violations)
    assert any("flight_agent.ports.flight_providers" in violation for violation in violations)


def test_provider_port_transport_dependency_negative_control_fails(tmp_path: Path) -> None:
    package_root = make_package_fixture(tmp_path)
    write_module(
        package_root / "ports" / "leak.py",
        "import httpx\n",
    )

    violations = collect_dependency_violations(package_root)

    assert any("ports-boundary" in violation for violation in violations)
    assert any("httpx" in violation for violation in violations)


def test_requirement_interpreter_fake_stays_outside_application_boundary() -> None:
    application_imports = {
        imported_module
        for module_path in (SOURCE_ROOT / "application").rglob("*.py")
        for imported_module in imported_modules(module_path)
    }

    assert "flight_agent.adapters.requirement_interpreter_fake" not in application_imports


def test_application_does_not_depend_on_mock_flight_provider() -> None:
    application_imports = {
        imported_module
        for module_path in (SOURCE_ROOT / "application").rglob("*.py")
        for imported_module in imported_modules(module_path)
    }

    assert "flight_agent.adapters.flight_providers.mock" not in application_imports
    assert "flight_agent.adapters.flight_providers.mock.provider" not in application_imports
    assert "flight_agent.adapters.flight_providers.mock.mapper" not in application_imports


def test_application_does_not_depend_on_llm_adapter_or_provider_specific_sdk() -> None:
    application_imports = {
        imported_module
        for module_path in (SOURCE_ROOT / "application").rglob("*.py")
        for imported_module in imported_modules(module_path)
    }
    application_source = "\n".join(
        module_path.read_text(encoding="utf-8")
        for module_path in (SOURCE_ROOT / "application").rglob("*.py")
    )

    assert "flight_agent.adapters.llm_fake" not in application_imports
    assert "flight_agent.adapters.llm_deepseek" not in application_imports
    assert "deepseek" not in application_imports
    assert "openai" not in application_imports
    assert "DeepSeek" not in application_source


def test_llm_capability_contract_does_not_own_m6_or_m7_authority() -> None:
    llm_contract = SOURCE_ROOT / "ports" / "llm_capabilities.py"
    imports = set(imported_modules(llm_contract))
    source = llm_contract.read_text(encoding="utf-8")

    assert "flight_agent.domain.decision" not in imports
    assert "flight_agent.domain.impact" not in imports
    assert "flight_agent.application.impact_orchestrator" not in imports
    assert "RecommendationSelector" not in source
    assert "ImpactDecision" not in source
    assert "ExecutionPlan" not in source
    assert "PublicationGuard" not in source


def test_llm_provider_sdk_dependency_negative_control_fails(tmp_path: Path) -> None:
    package_root = make_package_fixture(tmp_path)
    write_module(
        package_root / "ports" / "llm_capabilities.py",
        "import deepseek\n",
    )

    violations = collect_dependency_violations(package_root)

    assert any("ports-boundary" in violation for violation in violations)
    assert any("deepseek" in violation for violation in violations)


def test_m5_structured_api_does_not_import_requirement_state() -> None:
    structured_api = SOURCE_ROOT / "api" / "structured_entry.py"

    assert "flight_agent.domain.requirements" not in imported_modules(structured_api)
    assert "RequirementState" not in structured_api.read_text(encoding="utf-8")


def test_m5_api_does_not_call_provider_or_snapshot_pipeline_directly() -> None:
    api_imports = {
        imported_module
        for module_path in (SOURCE_ROOT / "api").rglob("*.py")
        for imported_module in imported_modules(module_path)
    }

    assert "flight_agent.adapters.flight_providers.mock" not in api_imports
    assert "flight_agent.ports.flight_providers" not in api_imports
    assert "flight_agent.domain.flights" not in api_imports


def test_m5_structured_entry_application_does_not_depend_on_provider_or_fixture() -> None:
    application_imports = {
        imported_module
        for module_path in (SOURCE_ROOT / "application").rglob("*.py")
        for imported_module in imported_modules(module_path)
    }
    application_source = "\n".join(
        module_path.read_text(encoding="utf-8")
        for module_path in (SOURCE_ROOT / "application").rglob("*.py")
    )

    assert "flight_agent.adapters.flight_providers.mock" not in application_imports
    assert "flight_agent.adapters.flight_providers.mock.provider" not in application_imports
    assert "flight_agent.adapters.flight_providers.mock.mapper" not in application_imports
    assert "fixtures/" not in application_source
    assert "mock_flight_provider_cases" not in application_source


def test_m5_minimal_decision_application_has_no_inline_filter_rank_or_select_shortcut() -> None:
    decision_application = SOURCE_ROOT / "application" / "minimal_decision.py"
    source = decision_application.read_text(encoding="utf-8")

    assert ".total_price" not in source
    assert "sorted(" not in source
    assert "ranked_candidates[0]" not in source
    assert "RecommendationItem(" not in source


def test_m5_publication_application_does_not_rerun_decision_or_provider_pipeline() -> None:
    publication_application = SOURCE_ROOT / "application" / "publication.py"
    imports = set(imported_modules(publication_application))
    source = publication_application.read_text(encoding="utf-8")

    assert "flight_agent.adapters.flight_providers.mock" not in imports
    assert "flight_agent.ports.flight_providers" not in imports
    assert "flight_agent.domain.decision" not in imports
    assert "LowerPriceRanking" not in source
    assert "MaxPriceFilter" not in source
    assert "RecommendationSelector" not in source
    assert "provider_result" not in source
    assert "mapping_result" not in source
    assert "fixtures/" not in source


def test_m5_public_api_projection_does_not_import_decision_or_provider_internals() -> None:
    structured_api = SOURCE_ROOT / "api" / "structured_entry.py"
    imports = set(imported_modules(structured_api))
    source = structured_api.read_text(encoding="utf-8")

    assert "flight_agent.domain.decision" not in imports
    assert "flight_agent.ports.flight_providers" not in imports
    assert "flight_agent.adapters.flight_providers.mock" not in imports
    assert "FilterResult" not in source
    assert "RankingResult" not in source
    assert "RecommendationResult" not in source


def test_m5_frontend_consumes_only_conversation_public_projection() -> None:
    frontend_source_root = REPO_ROOT / "apps" / "frontend" / "src"
    source = "\n".join(
        module_path.read_text(encoding="utf-8")
        for module_path in frontend_source_root.rglob("*")
        if module_path.suffix in {".ts", ".tsx"}
    )

    assert "/provider-search" not in source
    assert "/snapshot" not in source
    assert "/filter" not in source
    assert "/rank" not in source
    assert "/recommendation-result" not in source
    assert "fixtures" not in source
    assert "ProviderRawEvidence" not in source
    assert "filter_result" not in source
    assert "ranking_result" not in source
    assert "RequirementState" not in source
    assert "current_published_recommendation" in source
    assert "/conversations" in source


def test_m5_decision_domain_does_not_depend_on_provider_raw_fixture_publication_or_llm() -> None:
    decision_root = SOURCE_ROOT / "domain" / "decision"
    imports = {
        imported_module
        for module_path in decision_root.rglob("*.py")
        for imported_module in imported_modules(module_path)
    }
    source = "\n".join(
        module_path.read_text(encoding="utf-8")
        for module_path in decision_root.rglob("*.py")
    )

    assert "flight_agent.ports.flight_providers" not in imports
    assert "flight_agent.adapters.flight_providers.mock" not in imports
    assert "flight_agent.domain.workflow.publication" not in imports
    assert "fixtures/" not in source
    assert "ProviderRawEvidence" not in source
    assert "openai" not in source


def test_m6_evaluation_foundation_does_not_depend_on_downstream_decision_engines() -> None:
    evaluation_foundation = SOURCE_ROOT / "domain" / "decision" / "evaluation.py"
    imports = set(imported_modules(evaluation_foundation))

    assert "flight_agent.domain.decision.ranking" not in imports
    assert "flight_agent.domain.decision.selection" not in imports
    assert "flight_agent.application.minimal_decision" not in imports
    assert "flight_agent.application" not in imports


def test_m6_feature_engine_does_not_depend_on_downstream_engines_or_external_runtime() -> None:
    feature_engine = SOURCE_ROOT / "domain" / "decision" / "features.py"
    imports = set(imported_modules(feature_engine))
    source = feature_engine.read_text(encoding="utf-8")

    assert "flight_agent.domain.decision.ranking" not in imports
    assert "flight_agent.domain.decision.selection" not in imports
    assert "flight_agent.application" not in imports
    assert "flight_agent.ports.flight_providers" not in imports
    assert "flight_agent.adapters.flight_providers.mock" not in imports
    assert "fastapi" not in imports
    assert "sqlalchemy" not in imports
    assert "openai" not in imports
    assert "ProviderRawEvidence" not in source
    assert "normalized_value" not in source
    assert "aggregate_score" not in source


def test_m6_filtering_engine_does_not_depend_on_ranking_recommendation_or_external_runtime() -> None:
    filtering_engine = SOURCE_ROOT / "domain" / "decision" / "filtering.py"
    imports = set(imported_modules(filtering_engine))
    source = filtering_engine.read_text(encoding="utf-8")

    assert "flight_agent.domain.decision.ranking" not in imports
    assert "flight_agent.domain.decision.selection" not in imports
    assert "flight_agent.domain.decision.relaxation" not in imports
    assert "flight_agent.application" not in imports
    assert "flight_agent.ports.flight_providers" not in imports
    assert "flight_agent.adapters.flight_providers.mock" not in imports
    assert "fastapi" not in imports
    assert "sqlalchemy" not in imports
    assert "openai" not in imports
    assert "ProviderRawEvidence" not in source
    assert "ranking_score" not in source
    assert "recommendation_role" not in source


def test_m6_relaxation_engine_does_not_depend_on_provider_search_or_external_runtime() -> None:
    relaxation_engine = SOURCE_ROOT / "domain" / "decision" / "relaxation.py"
    imports = set(imported_modules(relaxation_engine))
    source = relaxation_engine.read_text(encoding="utf-8")

    assert "flight_agent.domain.decision.ranking" not in imports
    assert "flight_agent.domain.decision.selection" not in imports
    assert "flight_agent.application" not in imports
    assert "flight_agent.ports.flight_providers" not in imports
    assert "flight_agent.adapters.flight_providers.mock" not in imports
    assert "flight_agent.api" not in imports
    assert "fastapi" not in imports
    assert "sqlalchemy" not in imports
    assert "openai" not in imports
    assert "ProviderRawEvidence" not in source
    assert "SearchPlan" not in source
    assert "PatchSet" not in source


def test_m6_ranking_engine_does_not_depend_on_recommendation_relaxation_or_external_runtime() -> None:
    ranking_engine = SOURCE_ROOT / "domain" / "decision" / "ranking.py"
    imports = set(imported_modules(ranking_engine))
    source = ranking_engine.read_text(encoding="utf-8")

    assert "flight_agent.domain.decision.selection" not in imports
    assert "flight_agent.domain.workflow.recommendation" not in imports
    assert "flight_agent.domain.decision.relaxation" not in imports
    assert "flight_agent.application" not in imports
    assert "flight_agent.ports.flight_providers" not in imports
    assert "flight_agent.adapters.flight_providers.mock" not in imports
    assert "flight_agent.api" not in imports
    assert "fastapi" not in imports
    assert "sqlalchemy" not in imports
    assert "openai" not in imports
    assert "ProviderRawEvidence" not in source
    assert "RecommendationSelector" not in source
    assert "Relaxation" not in source


def test_m6_recommendation_selector_does_not_depend_on_publication_provider_or_external_runtime() -> None:
    selector = SOURCE_ROOT / "domain" / "decision" / "selection.py"
    imports = set(imported_modules(selector))
    source = selector.read_text(encoding="utf-8")

    assert "flight_agent.domain.workflow.publication" not in imports
    assert "flight_agent.domain.decision.relaxation" not in imports
    assert "flight_agent.application" not in imports
    assert "flight_agent.ports.flight_providers" not in imports
    assert "flight_agent.adapters.flight_providers.mock" not in imports
    assert "flight_agent.api" not in imports
    assert "fastapi" not in imports
    assert "sqlalchemy" not in imports
    assert "openai" not in imports
    assert "ProviderRawEvidence" not in source
    assert "PublishedRecommendation" not in source
    assert "Relaxation" not in source


def test_m7_orchestrator_stays_thin_and_out_of_business_correctness() -> None:
    orchestrator = SOURCE_ROOT / "application" / "impact_orchestrator.py"
    imports = set(imported_modules(orchestrator))
    source = orchestrator.read_text(encoding="utf-8")

    assert "flight_agent.adapters" not in imports
    assert "flight_agent.infrastructure" not in imports
    assert "flight_agent.api" not in imports
    assert "evaluate_snapshot" not in source
    assert "select_best_overall" not in source
    assert "ranked_candidates[0]" not in source
    assert "PublicationGuard" not in source
    assert "GUARDED_ATTEMPT" not in source


def test_m7_execution_guards_stay_in_process_and_out_of_outer_layers() -> None:
    guards = SOURCE_ROOT / "application" / "execution_guards.py"
    imports = set(imported_modules(guards))
    source = guards.read_text(encoding="utf-8")

    assert "flight_agent.adapters" not in imports
    assert "flight_agent.infrastructure" not in imports
    assert "flight_agent.api" not in imports
    assert "sqlalchemy" not in imports
    assert "celery" not in imports
    assert "kafka" not in imports
    assert "Temporal" not in source
    assert "distributed lock" not in source


def test_m6_feature_layer_does_not_depend_on_filtering_engine() -> None:
    feature_engine = SOURCE_ROOT / "domain" / "decision" / "features.py"
    imports = set(imported_modules(feature_engine))

    assert "flight_agent.domain.decision.filtering" not in imports
    assert "flight_agent.domain.decision.ranking" not in imports
    assert "flight_agent.domain.decision.selection" not in imports


def test_mock_flight_provider_stays_out_of_downstream_candidate_processing() -> None:
    violations = collect_provider_acl_downstream_violations(SOURCE_ROOT)

    assert violations == []


def test_mock_provider_downstream_dependency_negative_control_fails(tmp_path: Path) -> None:
    package_root = make_package_fixture(tmp_path)
    mock_root = package_root / "adapters" / "flight_providers" / "mock"
    mock_root.mkdir(parents=True)
    write_module(mock_root / "__init__.py", "")
    write_module(
        mock_root / "leak.py",
        "from flight_agent.domain.workflow.recommendation import Recommendation\n",
    )

    violations = collect_provider_acl_downstream_violations(package_root)

    assert any("flight_agent.domain.workflow.recommendation" in violation for violation in violations)


def test_provider_mapper_snapshot_dependency_negative_control_fails(tmp_path: Path) -> None:
    package_root = make_package_fixture(tmp_path)
    mock_root = package_root / "adapters" / "flight_providers" / "mock"
    mock_root.mkdir(parents=True)
    write_module(mock_root / "__init__.py", "")
    write_module(
        mock_root / "mapper.py",
        "from flight_agent.domain.flights.snapshot import CandidateSnapshot\n",
    )

    violations = collect_provider_acl_downstream_violations(package_root)

    assert any("flight_agent.domain.flights.snapshot" in violation for violation in violations)


def test_candidate_normalization_stays_out_of_downstream_and_provider_specific_boundaries() -> None:
    violations = collect_candidate_normalization_boundary_violations(SOURCE_ROOT)

    assert violations == []


def test_candidate_normalization_downstream_dependency_negative_control_fails(tmp_path: Path) -> None:
    package_root = make_package_fixture(tmp_path)
    write_module(
        package_root / "ports" / "candidate_normalization.py",
        "from flight_agent.domain.workflow.recommendation import Recommendation\n",
    )

    violations = collect_candidate_normalization_boundary_violations(package_root)

    assert any("flight_agent.domain.workflow.recommendation" in violation for violation in violations)


def test_snapshot_assembler_stays_out_of_downstream_and_provider_runtime_boundaries() -> None:
    violations = collect_snapshot_assembler_boundary_violations(SOURCE_ROOT)

    assert violations == []


def test_snapshot_assembler_downstream_dependency_negative_control_fails(tmp_path: Path) -> None:
    package_root = make_package_fixture(tmp_path)
    write_module(
        package_root / "application" / "snapshot_assembly.py",
        "from flight_agent.domain.workflow.recommendation import RecommendationResult\n",
    )

    violations = collect_snapshot_assembler_boundary_violations(package_root)

    assert any("flight_agent.domain.workflow.recommendation" in violation for violation in violations)


def collect_provider_acl_downstream_violations(package_root: Path) -> list[str]:
    mock_root = package_root / "adapters" / "flight_providers" / "mock"
    if not mock_root.exists():
        return []
    forbidden_imports = {
        "flight_agent.domain.workflow.recommendation",
        "flight_agent.domain.flights.snapshot",
        "httpx",
        "requests",
    }
    violations: list[str] = []
    for module_path in mock_root.rglob("*.py"):
        for imported_module in imported_modules(module_path):
            if imported_module in forbidden_imports:
                violations.append(
                    "mock-provider-boundary: "
                    f"{relative(module_path)} imports {imported_module}. "
                    "Mock provider must not depend on downstream candidate processing"
                )
    return violations


def collect_snapshot_assembler_boundary_violations(package_root: Path) -> list[str]:
    module_path = package_root / "application" / "snapshot_assembly.py"
    if not module_path.exists():
        return []
    forbidden_imports = {
        "flight_agent.adapters.flight_providers.mock",
        "flight_agent.adapters.flight_providers.mock.provider",
        "flight_agent.adapters.flight_providers.mock.mapper",
        "flight_agent.domain.workflow",
        "flight_agent.domain.workflow.recommendation",
        "flight_agent.domain.workflow.execution",
        "flight_agent.domain.workflow.publication",
        "httpx",
        "requests",
    }
    forbidden_names = {
        "Filter",
        "Ranking",
        "Recommendation",
        "ImpactResolver",
        "Reuse",
        "Refresh",
    }
    violations: list[str] = []
    for imported_module in imported_modules(module_path):
        if imported_module in forbidden_imports:
            violations.append(
                "snapshot-assembler-boundary: "
                f"{relative(module_path)} imports {imported_module}. "
                "Snapshot assembler must not depend on downstream policy or provider runtime"
            )
    source = module_path.read_text(encoding="utf-8")
    for forbidden_name in forbidden_names:
        if forbidden_name in source:
            violations.append(
                "snapshot-assembler-boundary: "
                f"{relative(module_path)} mentions {forbidden_name}. "
                "Snapshot assembler must not implement downstream policy"
            )
    return violations


def collect_candidate_normalization_boundary_violations(package_root: Path) -> list[str]:
    module_path = package_root / "ports" / "candidate_normalization.py"
    if not module_path.exists():
        return []
    forbidden_imports = {
        "flight_agent.adapters.flight_providers.mock",
        "flight_agent.adapters.flight_providers.mock.mapper",
        "flight_agent.domain.requirements",
        "flight_agent.domain.workflow",
        "flight_agent.domain.workflow.recommendation",
        "flight_agent.domain.flights.snapshot",
        "httpx",
        "requests",
    }
    violations: list[str] = []
    for imported_module in imported_modules(module_path):
        if imported_module in forbidden_imports:
            violations.append(
                "candidate-normalization-boundary: "
                f"{relative(module_path)} imports {imported_module}. "
                "Normalizer/Merger must not depend on downstream, provider-specific, or request authority"
            )
    return violations


def collect_dependency_violations(package_root: Path) -> list[str]:
    violations: list[str] = []
    for rule in RULES:
        layer_root = package_root / rule.layer
        if not layer_root.exists():
            violations.append(f"{rule.name}: missing layer package {layer_root}")
            continue
        for module_path in layer_root.rglob("*.py"):
            for imported_module in imported_modules(module_path):
                violation = validate_import(rule, module_path, imported_module)
                if violation is not None:
                    violations.append(violation)
    return violations


def validate_import(rule: DependencyRule, module_path: Path, imported_module: str) -> str | None:
    if imported_module == BACKEND_PACKAGE:
        imported_layer = ""
    elif imported_module.startswith(f"{BACKEND_PACKAGE}."):
        imported_layer = imported_module.split(".")[1]
    else:
        imported_layer = None

    if imported_layer is not None:
        if imported_layer not in rule.allowed_first_party_layers:
            return (
                f"{rule.name}: {relative(module_path)} imports {imported_module}. "
                f"{rule.forbidden_message}"
            )
        return None

    external_root = imported_module.split(".")[0]
    if external_root in rule.forbidden_external_roots:
        return (
            f"{rule.name}: {relative(module_path)} imports {imported_module}. "
            f"{rule.forbidden_message}"
        )
    return None


def imported_modules(module_path: Path) -> list[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    return imports


def make_package_fixture(tmp_path: Path) -> Path:
    package_root = tmp_path / BACKEND_PACKAGE
    for layer in (
        "domain",
        "ports",
        "application",
        "adapters",
        "infrastructure",
        "api",
        "bootstrap",
    ):
        layer_root = package_root / layer
        layer_root.mkdir(parents=True, exist_ok=True)
        write_module(layer_root / "__init__.py", "")
    return package_root


def write_module(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")


def relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)
