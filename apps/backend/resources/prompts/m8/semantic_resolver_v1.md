## CAPABILITY_INSTRUCTION

Resolve only relationships among deterministic evidence already present in the trusted context. The output is a non-authoritative semantic resolution for U6H-A/U6H-B deterministic builders, not a Requirement, Proposal, canonicalization, or commit decision.

## CONTRACT_CONSTRAINTS

Use only deterministic evidence IDs supplied in the trusted context. Use only relation_kind values from allowed_output_vocabulary. Do not invent origin, destination, dates, money values, cities, airports, IATA codes, constraints, preferences, mutation families, RequirementState, PatchSet, or authoritative IDs. For PARSER tasks, `ADD_SOFT_FEWER_STOPS_PREFERENCE` is authorized only when trusted evidence expresses direct flight as not mandatory but preferred, such as “不要求直飞，但我更喜欢直飞” or “直飞不是必须，但优先直飞”. This relation means the fixed canonical soft preference FEWER_STOPS/HIGH/value-null; it must not create a hard MAX_STOPS constraint and must leave target/value null. Always include supporting evidence_ids from the trusted context; if one evidence item contains the complete phrase, use that id, for example `["ev-unsupported-1"]`. For split parser evidence, prefer `parser_soft_fewer_stops_evidence_hints`; if the hint list is empty, do not emit `ADD_SOFT_FEWER_STOPS_PREFERENCE`. Free text diagnostics are never authoritative.

## OUTPUT_SCHEMA_GUIDANCE

Return exactly one JSON object with keys `request_id`, `status`, `relations`, `unresolved_items`, `diagnostics`, and `model_metadata`. Relation objects must use only `relation_kind`, `evidence_ids`, `target`, `value`, and `confidence`. `confidence` must be a JSON number between 0 and 1 or null, never a string. Example parser soft preference relation: `{"relation_kind":"ADD_SOFT_FEWER_STOPS_PREFERENCE","evidence_ids":["ev-unsupported-1"],"target":null,"value":null,"confidence":0.8}`.
