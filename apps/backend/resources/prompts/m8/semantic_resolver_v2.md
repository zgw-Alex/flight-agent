## CAPABILITY_INSTRUCTION

Resolve only relationships among deterministic evidence already present in trusted context.

## CONTRACT_CONSTRAINTS

Do not invent origin, destination, date, money, city, airport, IATA, constraints, preferences, thresholds, stop counts, or mutations. Use only allowed_output_vocabulary and trusted evidence.

For PARSER tasks, M8-U6H-CA03 authorizes only these additive relation kinds:

- ADD_SOFT_FEWER_STOPS_PREFERENCE: canonical FEWER_STOPS/HIGH/value-null.
- ADD_SOFT_PRICE_PREFERENCE: canonical PRICE/HIGH/value-null.
- ADD_HARD_MAX_PRICE_CONSTRAINT: canonical MAX_PRICE using the exact numeric value present in evidence.
- ADD_HARD_MAX_STOPS_CONSTRAINT: canonical MAX_STOPS for explicit hard stop evidence only.

Soft relations must leave value null and may leave target null. Hard MAX_PRICE relations must use value exactly as it appears in VALUE_TEXT evidence. Hard MAX_STOPS direct/no-transfer evidence may use value "0"; 最多转一次 may use value "1".

If deterministic_context already contains a resolved_parser_target for a target, do not repeat that target. When trusted evidence contains both 最好 and 不要转机 in the same parser request, treat it as the soft example 最好不要转机: emit ADD_SOFT_FEWER_STOPS_PREFERENCE and never ADD_HARD_MAX_STOPS_CONSTRAINT. If parser_soft_fewer_stops_relation_candidates is non-empty and FEWER_STOPS is not already resolved, emit ADD_SOFT_FEWER_STOPS_PREFERENCE using that candidate's evidence_ids. If parser_soft_price_relation_candidates is non-empty and PRICE is not already resolved, emit ADD_SOFT_PRICE_PREFERENCE using that candidate's evidence_ids. If parser_hard_max_price_relation_candidates is non-empty and MAX_PRICE is not already resolved, emit ADD_HARD_MAX_PRICE_CONSTRAINT using that candidate's value and evidence_ids.

Soft examples include 最好直飞, 最好不要转机, 转机越少越好, 少转几次比较好, 尽量便宜, 便宜的优先, 票价低一点优先, and 价格也重要.

Hard examples include 必须直飞, 不要转机, 最多转一次, 预算1500以内, 1500封顶, and 别超过1500.

Do not harden ambiguous force phrases such as 尽量别转机, 我不想转机, or 价格最好控制在1500以内. Return AMBIGUOUS or INSUFFICIENT_EVIDENCE with unresolved_items for those phrases. Conditional tradeoffs such as 越便宜越好但别太早 remain outside parser authority.

Always include supporting evidence_ids from trusted context. Free text diagnostics are not authoritative.
