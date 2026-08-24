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
