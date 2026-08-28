# M8 Initial Requirement Interpretation Runtime Prompt

Prompt family: m8.initial_requirement_interpretation
Prompt template version: initial-requirement-v2
Output schema version: m8-u1

## CAPABILITY_INSTRUCTION
Interpret the current user flight request as a semantic proposal for the M3
Requirement Pipeline. Return only an InitialRequirementProposal-shaped
structured output through the provider-neutral capability result contract.

## CONTRACT_CONSTRAINTS
The proposal is not RequirementState and has no commit authority. Preserve hard
constraints and soft preferences as separate proposal semantics. Preserve
ambiguity or insufficient context instead of guessing. Do not mark unspecified
optional details such as passenger count, cabin class, exact departure time, or
time preference as insufficient when the request already has enough explicit
route/date information for M3 validation. Treat explicit IATA airport codes as
airport identities, not ambiguous city names. Use source evidence and span hints
when available. Do not use candidate snapshots, provider raw data, rankings,
recommendations, patch history, or conversation history.
