# M8 Patch Understanding Runtime Prompt

Prompt family: m8.patch_understanding
Prompt template version: patch-understanding-v2
Output schema version: m8-u1

## CAPABILITY_INSTRUCTION
Interpret the current user message as a proposed semantic patch against the
provided authoritative Requirement projection. Return only a
PatchRequirementProposal-shaped structured output through the provider-neutral
capability result contract.

## CONTRACT_CONSTRAINTS
The proposal is not a committed PatchSet or RequirementVersion and has no
mutation authority. Preserve the base Requirement id and version lineage. Keep
operations minimal and targeted: changing origin must target only the origin
constraint, and adding a budget limit must add one MAX_PRICE hard constraint
without changing destination/date unless explicitly requested. Preserve
ambiguous references or insufficient context instead of guessing. Do not use
full conversation history, full patch history, candidate snapshots, provider raw
data, rankings, recommendations, impact decisions, execution plans, or
publication state.
