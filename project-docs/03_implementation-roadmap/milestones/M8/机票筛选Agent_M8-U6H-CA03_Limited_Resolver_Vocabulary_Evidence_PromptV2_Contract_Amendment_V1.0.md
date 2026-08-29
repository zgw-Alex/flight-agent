# M8-U6H-CA03 Limited Resolver Vocabulary Evidence Prompt V2 Contract Amendment

## Decision

M8-U6H-CA03 is an accepted limited additive Contract Amendment for the M8-U6H parser semantic resolver.

## Authorized Scope

CA03 authorizes the non-authoritative DeepSeek semantic resolver to map explicit deterministic parser evidence into these already-existing canonical parser semantics:

- `ADD_SOFT_FEWER_STOPS_PREFERENCE` -> `SoftPreference(scope=FEWER_STOPS, importance=HIGH, value=None)`
- `ADD_SOFT_PRICE_PREFERENCE` -> `SoftPreference(scope=PRICE, importance=HIGH, value=None)`
- `ADD_HARD_MAX_PRICE_CONSTRAINT` -> `HardConstraint(scope=MAX_PRICE, operator=AT_OR_BEFORE, value=<exact evidence money>)`
- `ADD_HARD_MAX_STOPS_CONSTRAINT` -> `HardConstraint(scope=MAX_STOPS, operator=AT_OR_BEFORE, value=<explicit hard stop evidence>)`

## Preserved Authority

The resolver remains schema-constrained, evidence-closed, and non-authoritative. It does not create Requirement state directly, does not bypass M3 Requirement authority, does not bypass M6 decision authority, and does not bypass M7 semantic diff / impact / execution authority.

CA03 does not authorize arbitrary conditional tradeoffs, inferred thresholds, new stop families, new direct-flight hard semantics outside `MAX_STOPS`, direct Requirement mutation, provider/search invocation, or any value not grounded in trusted evidence.

## Boundary Cases

Ambiguous force phrases such as `尽量别转机`, `我不想转机`, and `价格最好控制在1500以内` remain conservative and must not be unsafely hardened.

Conditional tradeoffs such as `预算1500以内，但如果直飞的话贵一点也可以` and `最好直飞，但如果便宜很多转一次也行` remain outside parser resolver authority.

## Implementation Result

Prompt V2 is promoted as `m8-u6h-e-semantic-resolver-prompt-v2` with contract version `m8-u6h-e-v1.0`.

The fixed 32-case real DeepSeek evaluation result for this amendment is:

- PASS: 27
- HUMAN REVIEW: 5
- FAIL: 0
- P0: 0
- P1: 0

HUMAN REVIEW remains limited to cases 14, 15, 22, 24, and 27.
