# Flight Agent Document Authority

## Purpose

This document defines how Codex and contributors decide which project documents are authoritative for Flight Agent V0 implementation work. It prevents historical references, implementation convenience, or incomplete bootstrap files from silently overriding approved product and engineering contracts.

## Authority Levels

From highest to lowest authority:

1. Latest explicit user instruction, acceptance decision, or approved Contract Amendment.
2. Formal Product and Architecture Contracts:
   - `project-docs/02_architecture-baseline/机票筛选Agent_第二阶段工程架构设计正式收口_V0.docx`
   - `project-docs/01_product-baseline/机票筛选Agent_第一阶段正式收口_V0.docx`
3. Third Stage implementation roadmap:
   - `project-docs/03_implementation-roadmap/机票筛选Agent_第三阶段实施与讨论大纲_V0.docx`
4. Current approved Milestone or Implementation Unit contract:
   - `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_第三阶段_M8_Real_LLM_Specification_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-CA01_MAX_STOPS_Hard_Constraint_Contract_Amendment_Specification_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-C_DeepSeek_Semantic_Resolver_Specification_V1.1.docx`
   - `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-B_Parser_Hybrid_Semantic_Pipeline_Specification_V1.1.docx`
   - `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-A_Patch_Hybrid_Semantic_Pipeline_Specification_V1.2.docx`
   - `project-docs/03_implementation-roadmap/milestones/M7/机票筛选Agent_第三阶段_M7_Patch_Impact_Orchestrator_Specification_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M7/机票筛选Agent_第三阶段_M7正式收口_V1.1.docx`
   - `project-docs/03_implementation-roadmap/milestones/M6/机票筛选Agent_第三阶段_M6_Complete_Decision_Engine正式收口_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M6/机票筛选Agent_第三阶段_M6_CA01_MAX_PRICE_Hard_Constraint_Contract_Amendment_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M6/机票筛选Agent_第三阶段_M6_Complete_Decision_Engine_Specification_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M5/机票筛选Agent_第三阶段_M5_Walking_Skeleton_Specification_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M5/机票筛选Agent_第三阶段_M5_Walking_Skeleton正式收口_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M4/机票筛选Agent_第三阶段_M4_Mock_Provider_Snapshot_Specification_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M4/机票筛选Agent_第三阶段_M4_Mock_Provider_Snapshot正式收口_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M3/机票筛选Agent_第三阶段_M3_Requirement_Pipeline_Specification_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M3/机票筛选Agent_第三阶段_M3_Requirement_Pipeline正式收口_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M2/机票筛选Agent_第三阶段_M2_Core_Domain_Contract_Specification_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M2/机票筛选Agent_第三阶段_M2_Implementation_Units与验收计划_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M2/机票筛选Agent_第三阶段_M2_Core_Domain_Contract正式收口_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M1/机票筛选Agent_第三阶段_M1_Architecture_Skeleton正式收口_V1.0.docx`
5. Development Environment Readiness documents, within their own readiness scope.
6. `project-docs/99_reference/` historical and traceability materials.
7. README, development docs, and code comments, which explain the current implementation but do not override higher-level authority.

## Current Normative Sources

- The Stage 1 closure document is the Product and Domain semantics baseline.
- The Stage 2 architecture closure document is the Engineering Architecture baseline for Third Stage implementation.
- The Stage 3 implementation roadmap controls milestone sequencing and unit-level delivery process.
- The M3 Requirement Pipeline Specification remains the Contract Authority for M3 Requirement Pipeline semantics; the M3 formal closure records that M3-U1 through M3-U5, Golden Scenarios GS-01 through GS-13, and Aggregate Exit Gate G1 through G9 are complete with Contract Amendment NONE. M3 status: CLOSED. Effective M3 Requirement Contract is augmented by M6-CA01 for the additive MAX_PRICE hard constraint and by M8-U6H-CA01 for the additive MAX_STOPS hard constraint: non-negative integer, canonical AT_OR_BEFORE / `candidate.stop_count <= max_stops`, with `MAX_STOPS=0` representing hard direct-flight semantics. All other M3 semantics remain unchanged.
- The M4 Mock Provider + Snapshot Specification remains the Contract Authority for M4 Provider acquisition, Mock Provider, Mapper, Normalizer, Merger, and CandidateSnapshot assembly semantics; the M4 formal closure records that M4-U1 through M4-U6, Golden Scenarios GS-01 through GS-09, Negative Controls NC-01 through NC-08, and Aggregate Exit Gate G1 through G10 are complete with Contract Amendment NONE. M4 status: CLOSED.
- The M5 Walking Skeleton Specification remains the Contract Authority for M5 integration semantics. The M5 formal closure records that M5-U1 through M5-U5, Golden Scenarios GS-01 through GS-05, GS-P1 Partial Usable at P1 scope, Determinism, Lineage, No-Shortcut, and Aggregate Exit Gate G1 through G10 are complete with Contract Amendment NONE. M5 status: CLOSED.
- The M6 Complete Decision Engine Specification remains the Contract Authority / Implementation & Acceptance Baseline for M6. It covers Derived Feature, Complete Filtering, Complete Ranking, Recommendation Selector, Deterministic Relaxation, Golden Scenarios, and Aggregate Exit Gate scope. M6-CA01 remains the accepted additive Contract Amendment Authority for MAX_PRICE as a formal Money-valued OFFER-scoped hard constraint and a VALUE_RELAXABLE family; CA01 preserves SearchPlan/Candidate Universe boundaries by default, does not authorize Requirement mutation, Patch commit, Search/Provider invocation, MAX_STOPS, provider-side price pushdown, FX conversion, M7 capability, or Publication Guard. The M6 Complete Decision Engine formal closure records M6-U1 through M6-U6, M6-CA01-I1, Golden Scenarios GS-01 through GS-08, P0-01 through P0-14, and Aggregate Exit Gate G1 through G12 as PASS, with Unified CI PASS and Additional Contract Amendment NONE. M6 status: CLOSED. Contract Amendment M6-CA01 ACCEPTED + IMPLEMENTED.
- The M7 Patch + Impact + Orchestrator Specification remains the Contract Authority / Implementation & Acceptance Baseline for M7. It covers Requirement Semantic Diff, ImpactDecision, DataAction, selective ExecutionPlan, execution concurrency, Version Guard, and Publication Guard, while preserving M1-M6 stable semantics. The M7 formal closure records M7-U1 through M7-U6, Golden Scenarios GS-01 through GS-14, and Aggregate Exit Gate G1 through G12 as PASS, with Contract Amendment NONE and current-main GitHub Actions CI PASS on run 33041817149. M7 status: CLOSED.
- The M8 Real LLM Specification is the Contract Authority / Implementation & Acceptance Baseline for M8. It covers provider-neutral LLM capability contracts, structured outputs, prompt/context architecture, invocation runtime, DeepSeek candidate evaluation, baseline promotion, security/telemetry boundaries, and aggregate Real Cloud exit gates, while preserving M1-M7 stable semantics. M8 status: READY / eligible for M8-U1 implementation.
- M8-U6H-CA01 is the accepted and implemented additive Contract Amendment Authority for MAX_STOPS as a formal non-negative-integer hard constraint. CA01 status: ACCEPTED + IMPLEMENTED + PASS. It preserves M3 Patch/Requirement authority, M6 decision authority, M7 SemanticDiff/Impact authority, and does not add a DIRECT_FLIGHT hard family or any broader stops family.
- M8-U6H-C DeepSeek Semantic Resolver Specification V1.1 is the current Implementation Unit Authority for the evidence-closed, schema-constrained DeepSeek semantic resolver. V1.1 supersedes V1.0 while retaining V1.0 as historical governance evidence. U6H-C may resolve relationships among deterministic evidence only when U6H-A or U6H-B has classified a case as SEMANTIC_RESOLVER_REQUIRED; it is not Requirement, Proposal, canonicalization, or commit authority. Existing DeepSeek typed settings are the configuration authority. Additional Contract Amendment: NONE.
- M8-U6H-B Parser Hybrid Semantic Pipeline Specification V1.1 is the current Implementation Unit Authority for deterministic-first Initial Requirement Parser Hybrid execution. V1.1 supersedes V1.0 while retaining V1.0 as historical governance evidence. U6H-B implementation status: PASS at commit `5f16b9ad96438821030cbb02cb7370c90c4b5848`; U6H-B status: CLOSED. Additional Contract Amendment: NONE.
- M8-U6H-A Patch Hybrid Semantic Pipeline Specification V1.2 is the current Implementation Unit Authority for deterministic-first Patch Hybrid execution and closure-aligned terminology. V1.2 supersedes V1.1 while retaining V1.1 and V1.0 as historical governance evidence. U6H-A implementation status: PASS at commit `9adf96f97c9b32b8827d152fc7e9beba37330310`; U6H-A status: CLOSED. V1.2 confirms hard direct-flight semantics remain canonical `MAX_STOPS=0`, soft direct-flight semantics use the existing `FEWER_STOPS` preference authority, and Additional Contract Amendment: NONE.
- The M2 Core Domain Contract Specification remains the Contract Authority for M2 Domain semantics; the M2 formal closure records that M2-U1 through M2-U5 and the M2 Aggregate Exit Gate are complete with Contract Amendment NONE.
- The M1 Architecture Skeleton closure controls the current architecture dependency guard and composition-root boundary, provided it does not conflict with higher authority.

## Reference And Traceability Sources

The following are reference and traceability sources unless explicitly promoted by a later approved amendment:

- `project-docs/99_reference/机票筛选 Agent 第二阶段工程架构设计.pdf`
- `project-docs/99_reference/机票筛选Agent_第二阶段_Phase1-19阶段性收口总结_V0.12.docx`
- `project-docs/99_reference/机票筛选Agent_Phase16_技术栈选择与开发环境准备_V0.docx`
- `project-docs/99_reference/机票筛选Agent_V0_项目仓库结构与PowerShell初始化指南.docx`

Phase 1-19 summaries are useful for traceability, but they are not default primary normative sources after formal closure documents exist.

## Superseded Documents

- `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_第三阶段_M8-U6H-A_Patch_Hybrid_Semantic_Pipeline_Specification_V1.0.docx` is superseded for active U6H-A authority by `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-A_Patch_Hybrid_Semantic_Pipeline_Specification_V1.1.docx`, which is superseded by `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-A_Patch_Hybrid_Semantic_Pipeline_Specification_V1.2.docx`; V1.0 remains historical governance evidence and must not be deleted. Approved decision: U6H-A V1.2 governance alignment; affected scope: M8-U6H-A Specification Authority; date of change: 2026-08-28.
- `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-A_Patch_Hybrid_Semantic_Pipeline_Specification_V1.1.docx` is superseded for active U6H-A authority by `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-A_Patch_Hybrid_Semantic_Pipeline_Specification_V1.2.docx`; V1.1 remains historical governance evidence and must not be deleted. The current V1.1 working-tree binary modification is human-originated accepted governance cleanup/alignment and does not introduce authority beyond V1.2. Approved decision: U6H-A V1.2 governance alignment; affected scope: M8-U6H-A Specification Authority; date of change: 2026-08-28.
- `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-B_Parser_Hybrid_Semantic_Pipeline_Specification_V1.0.docx` is superseded for active U6H-B authority by `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-B_Parser_Hybrid_Semantic_Pipeline_Specification_V1.1.docx`; V1.0 remains historical governance evidence and must not be deleted. Approved decision: U6H-B V1.1 specification intake; affected scope: M8-U6H-B Specification Authority; date of change: 2026-08-28.
- `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-C_DeepSeek_Semantic_Resolver_Specification_V1.0.docx` is superseded for active U6H-C authority by `project-docs/03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-C_DeepSeek_Semantic_Resolver_Specification_V1.1.docx`; V1.0 remains historical governance evidence and must not be deleted. Approved decision: U6H-C V1.1 specification intake; affected scope: M8-U6H-C Specification Authority; date of change: 2026-08-28.

To mark a document as superseded, record:

- document path
- superseding source
- approved decision or amendment
- affected scope
- date of change

## Contract Amendment Rules

Codex must stop and report evidence before changing any stable Product or Architecture Contract. This includes Domain semantics, Ports, Public APIs, Workflow State, Schema or Migration behavior, Architecture Rules, Security Policy, provider contracts, LLM integration contracts, or persistence contracts.

An approved Contract Amendment must include the decision, affected scope, rationale, and required updates to implementation, tests, fixtures, and documentation.

## Codex Read Order

For Implementation Unit execution, read the minimum sufficient authoritative context in this order:

1. `docs/DOCUMENT_AUTHORITY.md`
2. the current approved Implementation Unit or Milestone contract, including the M2 Core Domain Contract Specification, M2 Implementation Units plan, and M2 formal closure evidence during post-M2 work
3. the Stage 3 implementation roadmap sections relevant to the current milestone and unit
4. the Stage 2 architecture closure sections directly related to the change
5. the Stage 1 product closure sections if Product or Domain behavior is involved
6. reference materials only when traceability or historical detail is needed

Codex must not execute a broad reference script or historical repository layout solely because it exists in `project-docs/99_reference/`.

## Update Rules

Update this document when:

- a new authoritative milestone closure is approved
- a Contract Amendment changes authority or scope
- a source document is superseded
- the Codex read order changes
- a new normative source is added

Do not update this document for routine code changes that do not alter authority, scope, or read order.
