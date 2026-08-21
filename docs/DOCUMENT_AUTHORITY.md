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
   - `project-docs/03_implementation-roadmap/milestones/M2/机票筛选Agent_第三阶段_M2_Core_Domain_Contract_Specification_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M2/机票筛选Agent_第三阶段_M2_Implementation_Units与验收计划_V1.0.docx`
   - `project-docs/03_implementation-roadmap/milestones/M1/机票筛选Agent_第三阶段_M1_Architecture_Skeleton正式收口_V1.0.docx`
5. Development Environment Readiness documents, within their own readiness scope.
6. `project-docs/99_reference/` historical and traceability materials.
7. README, development docs, and code comments, which explain the current implementation but do not override higher-level authority.

## Current Normative Sources

- The Stage 1 closure document is the Product and Domain semantics baseline.
- The Stage 2 architecture closure document is the Engineering Architecture baseline for Third Stage implementation.
- The Stage 3 implementation roadmap controls milestone sequencing and unit-level delivery process.
- The M2 Core Domain Contract controls the current pure Domain representation work, starting with Shared Domain Primitives in M2-U1.
- The M1 Architecture Skeleton closure controls the current architecture dependency guard and composition-root boundary, provided it does not conflict with higher authority.

## Reference And Traceability Sources

The following are reference and traceability sources unless explicitly promoted by a later approved amendment:

- `project-docs/99_reference/机票筛选 Agent 第二阶段工程架构设计.pdf`
- `project-docs/99_reference/机票筛选Agent_第二阶段_Phase1-19阶段性收口总结_V0.12.docx`
- `project-docs/99_reference/机票筛选Agent_Phase16_技术栈选择与开发环境准备_V0.docx`
- `project-docs/99_reference/机票筛选Agent_V0_项目仓库结构与PowerShell初始化指南.docx`

Phase 1-19 summaries are useful for traceability, but they are not default primary normative sources after formal closure documents exist.

## Superseded Documents

No documents are marked superseded in M1.

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
2. the current approved Implementation Unit or Milestone contract, including the M2 Core Domain Contract Specification and M2 Implementation Units plan during M2 work
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
