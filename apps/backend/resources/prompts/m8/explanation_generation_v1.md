# M8 Explanation Generation Runtime Prompt

Prompt family: m8.explanation_generation
Prompt template version: explanation-generation-v1
Output schema version: m8-u1

## CAPABILITY_INSTRUCTION
Render a user-facing explanation draft from the approved deterministic evidence
projection. Return only an ExplanationDraft-shaped structured output through
the provider-neutral capability result contract.

## CONTRACT_CONSTRAINTS
The draft is not RecommendationResult and has no decision, ranking, mutation, or
publication authority. Use only approved evidence references and the approved
evidence projection. Preserve UNKNOWN or missing evidence as unknown. Do not
create facts, change the recommendation, expose provider raw data, use full
candidate snapshots, or use internal aggregate scores.
