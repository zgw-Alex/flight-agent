"""Fixture-driven fake LLM capabilities for offline M8 contract tests."""

from __future__ import annotations

from dataclasses import dataclass

from flight_agent.ports import (
    CapabilityFailure,
    CapabilityFailureKind,
    CapabilityGenerationMetadata,
    CapabilityResult,
    ExplanationDraft,
    ExplanationGenerationRequest,
    InitialRequirementInterpretationRequest,
    InitialRequirementProposal,
    LLMCapabilityName,
    PatchRequirementProposal,
    PatchUnderstandingRequest,
)


@dataclass(frozen=True)
class FakeInitialRequirementFixture:
    user_message: str
    result: CapabilityResult[InitialRequirementProposal]


@dataclass(frozen=True)
class FakePatchUnderstandingFixture:
    user_message: str
    requirement_projection: str
    result: CapabilityResult[PatchRequirementProposal]


@dataclass(frozen=True)
class FakeExplanationFixture:
    recommendation_result_id: str
    result: CapabilityResult[ExplanationDraft]


class FakeInitialRequirementLLM:
    def __init__(self, fixtures: tuple[FakeInitialRequirementFixture, ...]) -> None:
        self._fixtures = {fixture.user_message: fixture for fixture in fixtures}

    def interpret_initial_requirement(
        self, request: InitialRequirementInterpretationRequest
    ) -> CapabilityResult[InitialRequirementProposal]:
        fixture = self._fixtures.get(request.user_message)
        if fixture is None:
            return CapabilityResult.failure_result(
                _metadata(LLMCapabilityName.INITIAL_REQUIREMENT_INTERPRETATION),
                CapabilityFailure(
                    kind=CapabilityFailureKind.SCHEMA_INVALID,
                    code="FIXTURE_NOT_FOUND",
                    message="No deterministic initial requirement fixture matched the input",
                ),
            )
        return fixture.result


class FakePatchUnderstandingLLM:
    def __init__(self, fixtures: tuple[FakePatchUnderstandingFixture, ...]) -> None:
        self._fixtures = {
            (fixture.user_message, fixture.requirement_projection): fixture for fixture in fixtures
        }

    def understand_patch(
        self, request: PatchUnderstandingRequest
    ) -> CapabilityResult[PatchRequirementProposal]:
        fixture = self._fixtures.get((request.user_message, request.current_requirement_projection))
        if fixture is None:
            return CapabilityResult.failure_result(
                _metadata(LLMCapabilityName.PATCH_UNDERSTANDING),
                CapabilityFailure(
                    kind=CapabilityFailureKind.SCHEMA_INVALID,
                    code="FIXTURE_NOT_FOUND",
                    message="No deterministic patch fixture matched the input",
                ),
            )
        return fixture.result


class FakeExplanationLLM:
    def __init__(self, fixtures: tuple[FakeExplanationFixture, ...]) -> None:
        self._fixtures = {fixture.recommendation_result_id: fixture for fixture in fixtures}

    def generate_explanation(
        self, request: ExplanationGenerationRequest
    ) -> CapabilityResult[ExplanationDraft]:
        fixture = self._fixtures.get(request.recommendation_result_id.value)
        if fixture is None:
            return CapabilityResult.failure_result(
                _metadata(LLMCapabilityName.EXPLANATION_GENERATION),
                CapabilityFailure(
                    kind=CapabilityFailureKind.SCHEMA_INVALID,
                    code="FIXTURE_NOT_FOUND",
                    message="No deterministic explanation fixture matched the input",
                ),
            )
        return fixture.result


def _metadata(capability: LLMCapabilityName) -> CapabilityGenerationMetadata:
    return CapabilityGenerationMetadata(
        capability=capability,
        output_schema_version="m8-u1",
        adapter_version="fake-llm-u1",
    )
