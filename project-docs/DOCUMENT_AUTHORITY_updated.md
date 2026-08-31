# 机票筛选 Agent V0 --- Document Authority

**适用阶段：** 第三阶段开发环境准备与 M0--M12 实施\
**建议位置：** `D:\flight-agent\project-docs\DOCUMENT_AUTHORITY.md`

## 1. 目的

本文件定义 `project-docs`
内项目文档的权威优先级、适用范围、阅读顺序与冲突处理规则，供 Codex 在
Development Environment Readiness、Milestone / Implementation Unit
规划、编码、测试、验收、Contract Amendment 与架构追溯时使用。

Codex 不应将所有文档视为同等权威。正式收口文件定义稳定 Product /
Architecture
Contract；第三阶段实施大纲负责规定"按什么顺序实施与验收"；当前 Milestone
/ Implementation Unit 规格负责把上游 Contract 转化为本次可执行
Scope。历史 Reference 仅用于追溯，不得覆盖正式收口结论。

## 2. 文档权威优先级

从高到低：

1.  **用户最新明确确认的指令 / Acceptance / Approved Contract
    Amendment**
    -   仅当用户明确批准修改既有 Contract 时，才构成 Contract
        Amendment。
    -   临时实现便利、Codex 推测、自动重构或工具限制本身不构成 Contract
        Amendment。
2.  **正式 Product / Architecture Contract**
    -   `02_architecture-baseline/机票筛选Agent_第二阶段工程架构设计正式收口_V0.docx`
        -   第三阶段工程实现与验收的主要 Architecture Source of Truth。
        -   权威范围包括系统边界、Domain / Application / Ports /
            Adapters / Infrastructure 依赖方向、Data Contract、LLM /
            Flight Provider / Persistence / Workflow / API / Frontend
            架构、质量与安全基线、正式技术栈、Deployment、Repository
            Architecture、M0--M12 Roadmap 与 Codex Governance。
    -   `01_product-baseline/机票筛选Agent_第一阶段正式收口_V0.docx`
        -   Product / Requirement / Domain behavior 的主要上游基线。
        -   用于确认 V0 产品范围、Requirement 语义、Domain
            Model、Ranking、Recommendation、Explanation、异常与边界场景等产品/领域约束。
    -   两者职责不同；工程实现不得用 Architecture 便利静默改变 Product /
        Domain 语义。
3.  **第三阶段实施主控路线图**
    -   `03_implementation-roadmap/机票筛选Agent_第三阶段实施与讨论大纲_V0.docx`
    -   定义第三阶段从 M0 到 M12 的实施顺序、Milestone 目标、Exit
        Gate、Implementation Unit 标准格式、横切工作流、Contract
        Amendment 触发机制与对话/验收组织方式。
    -   该文档回答"如何把已收口 Contract 转化为可运行
        MVP"，不得覆盖第一、第二阶段正式 Contract。
    -   若正式收口已经锁定某项语义，而实施大纲仅给出建议 Unit
        拆分或执行顺序，以正式收口为语义上游。
4.  **当前 Milestone / Implementation Unit 已确认规格**
    -   `03_implementation-roadmap/milestones/M9/机票筛选Agent_第三阶段_M9_Real_Shopping_Provider_Implementation_Specification_V0.1.docx`
        -   第三阶段 M9 One Real Flight Provider 的正式 Contract Authority / Implementation & Acceptance Baseline。
        -   M9 status：OPEN / IN PROGRESS；授权真实 Provider evaluation 与受控 acquisition implementation，但不等于 M9 closure，也不因本次 intake 最终 SELECTED 首个 Provider。
        -   不改写 M4 Provider/Snapshot、M6 Decision、M7 Impact/Execution、M8 Real LLM 或上游 Product/Architecture Contract；如需改变稳定 Contract，必须进入单独 Contract Amendment。
    -   `03_implementation-roadmap/milestones/M9/机票筛选Agent_M9-BP5-U1_Fliggy_Browser_Acquisition_Read-Only_Probe_Specification_V0.1.docx`
        -   当前授权的第一个 M9 Implementation Unit。
        -   仅授权 FLIGGY + BROWSER 的 bounded / opt-in / read-only Probe，采集 Level-1 provider-side raw evidence 与明确 run outcome；不实现正式 Fliggy FlightProvider Adapter/Mapper、Level-2 Offer crawl、最终 PurchaseAccess 或 canonical Domain mapping。
        -   U1 implementation status：COMPLETE / IMPLEMENTED at commit `7a29c4f2d2bd921d423551bc9caa3556b93acb83`；Domain / Provider Port / Public API Contract Amendment：NONE。
    -   `03_implementation-roadmap/milestones/M9/机票筛选Agent_M9-BP5-U2_Fliggy_Browser_Probe_Live_Diagnosis_and_Repeatability_Specification_V0.1.docx`
        -   当前授权的下一个 M9 Implementation Unit。
        -   仅授权 BP5-U1 probe-local observability、stage diagnostics、bounded opt-in live repeatability experiments、diagnosis aggregation，以及 direct evidence 支持时的最小修复。
        -   不完成 M9，不最终 SELECTED FLIGGY 为 First Provider，不授权 Level-2 full Offer crawl、正式 FLIGGY Adapter/Mapper、PurchaseAccess implementation、Domain/Provider Port/Public API change 或 Browser Acquisition security bypass。
    -   `03_implementation-roadmap/milestones/M9/机票筛选Agent_M9-BP6-CA01_Lower-Bound_Offer_Price_Semantics_Contract_Amendment_Proposal_V0.1.docx`
        -   M9-BP6-CA01 Lower-Bound Offer Price Semantics 的正式 human-approved Contract Amendment governance authority。
        -   CA01 Decision Status：APPROVED；Implementation Status：NOT IMPLEMENTED。
        -   仅授权 price semantics 范围内的正式 CA01 contract/specification work 与后续明确授权的 implementation units；本 Proposal intake 不直接授权 source-code implementation。
        -   影响范围限于 M2 Offer price semantics、M4 MappedOffer / Common Normalizer / Offer commercial equivalence、M6 MAX_PRICE / Ranking price-evidence safety，以及 lower-bound 对用户暴露时的 API/UI semantic preservation。
        -   明确不改变 Money、SearchPlan、ProviderRawEvidence architecture、CandidateSnapshot top-level structure、M7 ENRICH core semantic、current persistence、FLIGGY Browser Probe selectors、Level-2 acquisition 或 broader commercial model。
    -   `03_implementation-roadmap/milestones/M9/机票筛选Agent_M9_Browser_Acquisition_Provider_Exploration_and_Collaboration_Playbook_V1.0.docx`
        -   M9 Track B 多 Provider Browser acquisition 的 supporting methodology / collaboration governance。
        -   用于统一 Hard Gate、Evidence Grade、Outcome、Coverage、安全边界与多人 Git 同步语言；不得提升为稳定 Domain/Provider Contract。
    -   `03_implementation-roadmap/milestones/M9/机票筛选Agent_M9_TrackB_Fliggy_Browser_Acquisition_Pilot_BP1-BP4阶段性实验结论_V0.2.docx`
        -   FLIGGY Browser Pilot B-P1～B-P4 的 live experiment / supporting evidence。
        -   记录人工探索结论和下一工程入口；不替代 M9 Formal Specification，不构成 M9 closure 或最终 Provider selection。
    -   `03_implementation-roadmap/milestones/M7/机票筛选Agent_第三阶段_M7_Patch_Impact_Orchestrator_Specification_V1.0.docx`
        -   第三阶段 M7 Patch + Impact + Orchestrator 的正式 Contract Authority / Implementation & Acceptance Baseline。
        -   权威范围包括 Requirement Semantic Diff、ImpactDecision、DataAction、selective ExecutionPlan、execution concurrency、Version Guard 与 Publication Guard。
        -   M7 Specification 保持为 Contract Authority；M7 formal closure 已记录 M7-U1～M7-U6、GS-01～GS-14、Aggregate Exit Gate G1～G12 全部 PASS；Contract Amendment：NONE。
        -   不重新定义 M1～M6 已稳定 Authority；不得为了 M7 实现静默改变 Requirement、Snapshot、Decision、Publication 或 Architecture Contract。
    -   `03_implementation-roadmap/milestones/M7/机票筛选Agent_第三阶段_M7正式收口_V1.1.docx`
        -   第三阶段 M7 Patch + Impact + Orchestrator 的正式 Milestone Closure Evidence。
        -   记录 M7-U1～M7-U6、GS-01～GS-14、Aggregate Exit Gate G1～G12、current-main GitHub Actions CI run 33041817149 已 PASS。
        -   Contract Amendment：NONE；M7 Milestone Status：CLOSED。
        -   不替代 M7 Patch + Impact + Orchestrator Specification 的 Contract Authority，也不启动 M8 implementation。
    -   `03_implementation-roadmap/milestones/M8/机票筛选Agent_第三阶段_M8_Real_LLM_Specification_V1.0.docx`
        -   第三阶段 M8 Real LLM 的正式 Contract Authority / Implementation & Acceptance Baseline。
        -   权威范围包括 provider-neutral LLM capability contracts、structured outputs、prompt/context architecture、invocation runtime、DeepSeek candidate evaluation、baseline promotion、security/telemetry boundaries 与 Aggregate Exit Gate G1～G17。
        -   M8 Specification 保持为 Contract Authority；M8 formal closure 已记录 M8-U1～M8-U7、Hybrid Recovery U6H-A/B/C/D、U6H-CA01、historical Parser/Patch Real LLM P0 failure recovery、U6H-D evidence bundle、Aggregate Exit Gate G1～G17 与 2026-08-29 unified CI 全部 PASS；Additional Contract Amendment：NONE。
    -   `03_implementation-roadmap/milestones/M8/机票筛选Agent_第三阶段_M8_Real_LLM正式收口_V1.0.docx`
        -   第三阶段 M8 Real LLM 的正式 Milestone Closure Evidence。
        -   记录 M8-U1～M8-U7、U6H-A/B/C/D、CA01、原 Parser/Patch Real LLM baseline P0 blocker、Hybrid recovery、U6H-D evidence artifact、Aggregate Exit Gate G1～G17、Real-path P0 3-run、ordinary CI network independence 与 repository integrity 已 PASS。
        -   Contract Amendment：CA01 ACCEPTED + IMPLEMENTED + PASS；Additional Contract Amendment：NONE；M8 Milestone Status：CLOSED / PASS。
        -   不替代 M8 Real LLM Specification 的 Contract Authority；其 closure 当时使 M9 进入 READY / eligible for planning and specification。当前 M9 authority 由已纳入的 M9 Real Shopping Provider Implementation Specification V0.1 管辖。
    -   `03_implementation-roadmap/milestones/M8/机票筛选Agent_第三阶段_M8正式收口_V1.0.docx`
        -   第三阶段 M8 Real LLM / Hybrid Prototype 的正式 Repository Closure Evidence。
        -   记录 RU1 Blocking Taxonomy、RU2 Initial Progressive Interpretation、RU3 Resolver Eligibility & Routing、RU4 Patch Residue Classification & Atomicity 全部 PASS。
        -   记录 Closure Decision：`M8_RECOVERY_CLOSURE_READY_WITH_P2_GAPS`；CG1～CG10 全部 PASS；P0 findings：none；P1 findings：none；accepted P2 coverage gaps：departure-time preference、aircraft preference、airline-quality / comfort preference。
        -   记录 final unified backend：651 passed, 2 skipped；Initial Blocking Decision Accuracy：29/29；Initial Unnecessary Clarification Rate：0/24；Initial Unsafe Continuation：0/5；Initial Usable Search Recall：24/24；Patch Blocking Decision Accuracy：14/14；Patch Unsafe Partial Commit：0/5；Patch atomicity violations：0；Resolver Eligibility Precision：3/3；Resolver Eligibility Recall：3/3；Supported Top-K：7/7 deterministic；Explanation：43 tests PASS。
        -   Baseline commit：`42faf4841506cacecc4b435c3906d863edef8b5e`；Contract Amendment：NONE；M8 Milestone Status：CLOSED；M8 closure 使 M9 READY / eligible。当前 M9 status 见 M9 authority 条目。
        -   不替代 M8 Real LLM Specification 的 Contract Authority；不得将 provider exposed fields 静默提升为 Domain-supported semantics 或 M6 ranking semantics。Accepted P2 gaps 的未来正式支持需要单独 capability / contract review。
    -   `03_implementation-roadmap/milestones/M8/机票筛选Agent_M8_U6H-C_Parser_Resolver_Soft_Preference_Contract_Amendment_CA02_V0.1.docx`
        -   第三阶段 M8 U6H-C Parser Resolver Soft Preference 的正式 additive Contract Amendment Authority。
        -   CA02 Decision Status：ACCEPTED；Implementation Authority：ENABLED。
        -   仅授权 `ADD_SOFT_FEWER_STOPS_PREFERENCE` 关系映射到既有 canonical `SoftPreference(scope=FEWER_STOPS, importance=HIGH, value=None)`。
        -   U6H-C base authority 保持有效；Resolver 保持 evidence-closed / non-authoritative semantic proposal only；M3 Requirement authority 与 M7 semantic diff / impact / execution authority 保持不变。
    -   `03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-CA01_MAX_STOPS_Hard_Constraint_Contract_Amendment_Specification_V1.0.docx`
        -   第三阶段 M8-U6H Recovery 的正式 additive Contract Amendment Authority。
        -   对 M3 Requirement Contract 仅增量补充 `MAX_STOPS`：formal Hard Constraint、non-negative integer value、canonical `AT_OR_BEFORE` / `candidate.stop_count <= max_stops`，其中 `MAX_STOPS=0` 表示 Hard “必须直飞”。
        -   不新增 `DIRECT_FLIGHT` Hard family，不批准 MIN_STOPS / EXACT_STOPS / stop-airport / layover-duration 等额外 Stops family。
        -   CA01 Decision Status：ACCEPTED；Implementation Status：COMPLETE；Acceptance Status：PASS。
    -   `03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-D_Hybrid_Eval_and_Baseline_Recovery_Specification_V1.1.docx`
        -   第三阶段 M8-U6H-D Hybrid Eval and Baseline Recovery 的当前 Implementation Unit Authority。
        -   V1.1 定义 Hybrid evaluation recovery 与 baseline-promotion evidence；评价 deterministic ownership、clarification zero-call safety、real resolver evidence path、fixed P0 stability、baseline identity 与 auditable metrics。
        -   V1.1 取代 V1.0 作为当前 U6H-D 执行依据；V1.0 保留为历史治理证据并标记 SUPERSEDED BY V1.1。U6H-D 不新增 Parser/Patch/Resolver product capability，不自创 P1/P2 baseline acceptance threshold。U6H-D implementation/eval commit：`34c7d9273fa641ffab0b616454513f67e4088844`；U6H-D status：CLOSED / PASS；focused tests、backend CI、unified CI、T4 real smoke 与 T3 real P0 3-run eval 已于 2026-08-28 PASS；Additional Contract Amendment：NONE。
    -   `03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-C_DeepSeek_Semantic_Resolver_Specification_V1.1.docx`
        -   第三阶段 M8-U6H-C DeepSeek Semantic Resolver 的当前 Implementation Unit Authority。
        -   V1.1 定义 evidence-closed、schema-constrained DeepSeek semantic resolver；DeepSeek 只可在 U6H-A / U6H-B deterministic front-half 已分类为 `SEMANTIC_RESOLVER_REQUIRED` 后解析既有 evidence 之间的关系。
        -   V1.1 取代 V1.0 作为当前 U6H-C 执行依据；V1.0 保留为历史治理证据并标记 SUPERSEDED BY V1.1。U6H-C 不成为 Requirement、Proposal、canonicalization 或 commit authority；复用现有 DeepSeek typed settings。U6H-C implementation commit：`8a6ce9119e6dfcb12987629a6b8d6500a0f3d66e`；U6H-C status：CLOSED / PASS；focused tests、backend CI、unified CI 与 explicit real DeepSeek smoke 已于 2026-08-28 PASS；Additional Contract Amendment：NONE。
    -   `03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-B_Parser_Hybrid_Semantic_Pipeline_Specification_V1.1.docx`
        -   第三阶段 M8-U6H-B Parser Hybrid Semantic Pipeline 的当前 Implementation Unit Authority。
        -   V1.1 定义 deterministic-first Initial Requirement Parser Hybrid 前半段，输出既有 InitialRequirementProposal 并复用 M3 Normalization / Validation / Policy / Commit Authority。
        -   V1.1 取代 V1.0 作为当前 U6H-B 执行依据；V1.0 保留为历史治理证据并标记 SUPERSEDED BY V1.1。U6H-B implementation commit：`5f16b9ad96438821030cbb02cb7370c90c4b5848`；U6H-B status：CLOSED / PASS；Additional Contract Amendment：NONE。
    -   `03_implementation-roadmap/milestones/M8/机票筛选Agent_M8-U6H-A_Patch_Hybrid_Semantic_Pipeline_Specification_V1.2.docx`
        -   第三阶段 M8-U6H-A Patch Hybrid Semantic Pipeline 的当前 Implementation Unit Authority。
        -   V1.2 是 U6H-A 实现 PASS 后的 terminology / implementation alignment authority；V1.2 继承 V1.1 的 deterministic Patch Hybrid 行为边界、Golden Scenarios、Exit Gates 与 M3 Patch authority。
        -   V1.2 确认 Hard “必须直飞” 的 canonical 表达仍为 `MAX_STOPS=0`，Soft “最好直飞” 使用既有 `FEWER_STOPS` preference authority，不新增 `DIRECT_FLIGHT` Hard family 或新的 Soft Preference family。
        -   V1.2 取代 V1.1 作为当前 U6H-A Specification Authority；V1.1 保留为历史治理证据并标记 SUPERSEDED BY V1.2；V1.0 保留为历史治理证据并标记 SUPERSEDED。U6H-A implementation commit：`9adf96f97c9b32b8827d152fc7e9beba37330310`；U6H-A status：CLOSED / PASS；Additional Contract Amendment：NONE。
        -   当前 V1.1 working-tree binary modification 是 HUMAN-ORIGINATED / ACCEPTED pre-existing governance cleanup/alignment，不引入超越 V1.2 的新 authority。
    -   `03_implementation-roadmap/milestones/M6/机票筛选Agent_第三阶段_M6_Complete_Decision_Engine正式收口_V1.0.docx`
        -   第三阶段 M6 Complete Decision Engine 的正式 Milestone Closure Evidence。
        -   记录 M6-U1～M6-U6、M6-CA01-I1、GS-01～GS-08、P0-01～P0-14 与 Aggregate Exit Gate G1～G12 已 PASS。
        -   CA01 状态：ACCEPTED + IMPLEMENTED；Additional Contract Amendment：NONE。
        -   M6 Milestone Status：CLOSED。
        -   不替代 M6 Complete Decision Engine Specification 的 Contract Authority，也不启动 M7 implementation。
    -   `03_implementation-roadmap/milestones/M6/机票筛选Agent_第三阶段_M6_CA01_MAX_PRICE_Hard_Constraint_Contract_Amendment_V1.0.docx`
        -   第三阶段 M6 的正式 additive Contract Amendment Authority。
        -   对 M3 Requirement Contract 仅增量补充 `MAX_PRICE`：formal Hard Constraint、Money-valued、OFFER scope，并保持与 `PRICE` Soft Preference 分离。
        -   默认不改变 SearchPlan / Candidate Universe，不授权 Provider-side price pushdown、FX conversion、RequirementState mutation、Patch commit、Search/Provider invocation、MAX_STOPS、M7 capability 或 Publication Guard。
        -   CA01 Decision Status：ACCEPTED；Implementation Status：COMPLETE。
    -   `03_implementation-roadmap/milestones/M6/机票筛选Agent_第三阶段_M6_Complete_Decision_Engine_Specification_V1.0.docx`
        -   第三阶段 M6 Complete Decision Engine 的正式 Contract Authority / Implementation & Acceptance Baseline。
        -   权威范围包括 Derived Feature、Complete Filtering、Complete Ranking、Recommendation Selector、Deterministic Relaxation、Golden Scenarios 与 Aggregate Exit Gate。
        -   M6 Specification 已接纳；M6 Implementation Status：COMPLETE；M6 Milestone 已 CLOSED。
        -   不重新定义 M2/M3/M4/M5 已稳定 Authority；不提前定义 M7 Semantic Diff、ImpactDecision、SEARCH / REFRESH / ENRICH / REUSE、execution lifecycle 或完整 Publication Guard。
    -   `03_implementation-roadmap/milestones/M5/机票筛选Agent_第三阶段_M5_Walking_Skeleton_Specification_V1.0.docx`
        -   第三阶段 M5 Walking Skeleton 的正式 Contract Authority。
        -   权威范围包括 Structured Entry、Search Execution Composition、Minimal Decision、Publication、Public Projection、Minimal Frontend 与 Aggregate Golden E2E。
        -   不重新定义 M2/M3/M4 已稳定 Authority，不提前实现 M6 Complete Decision Engine 或 M7 后续能力。
    -   `03_implementation-roadmap/milestones/M5/机票筛选Agent_第三阶段_M5_Walking_Skeleton正式收口_V1.0.docx`
        -   第三阶段 M5 Walking Skeleton 的正式 Milestone Closure Evidence。
        -   记录 M5-U1～M5-U5、Golden Scenario Matrix 与 Aggregate Exit Gate G1～G10 已 PASS，Contract Amendment NONE。
        -   M5 Milestone Status：CLOSED。
        -   不替代 M5 Walking Skeleton Specification 的 Contract Authority。
    -   `03_implementation-roadmap/milestones/M4/机票筛选Agent_第三阶段_M4_Mock_Provider_Snapshot_Specification_V1.0.docx`
        -   第三阶段 M4 Mock Provider + Snapshot 的正式 Contract Authority。
        -   权威范围包括 SearchPlan、FlightProvider Port、Mock Provider、Provider Mapper、Common Normalizer、Candidate Merger、CandidateSnapshotAssembler、Golden Scenarios 与 Aggregate Exit Gate。
        -   不重新定义 M2 Core Domain Contract 或 M3 Requirement Pipeline，不提前定义 M5/M6 后续能力。
    -   `03_implementation-roadmap/milestones/M4/机票筛选Agent_第三阶段_M4_Mock_Provider_Snapshot正式收口_V1.0.docx`
        -   第三阶段 M4 Mock Provider + Snapshot 的正式 Milestone Closure Evidence。
        -   记录 M4 Implementation Units、Golden Scenarios 与 Aggregate Exit Gate 已 PASS，Contract Amendment NONE。
        -   M4 Milestone Status：CLOSED。
        -   不替代 M4 Mock Provider + Snapshot Specification 的 Contract Authority。
    -   `03_implementation-roadmap/milestones/M3/机票筛选Agent_第三阶段_M3_Requirement_Pipeline_Specification_V1.0.docx`
        -   第三阶段 M3 Requirement Pipeline 的正式 Contract Authority。
        -   权威范围包括 INITIAL/PATCH pipeline、Interpreter boundary、Normalization/Validation、RequirementRepository、Patch construction/application、Golden Scenarios 与 Aggregate Exit Gate。
        -   不重新定义 M2 Core Domain Contract，不提前定义 M4 Provider/Snapshot 或后续 Milestone 能力。
    -   `03_implementation-roadmap/milestones/M3/机票筛选Agent_第三阶段_M3_Requirement_Pipeline正式收口_V1.0.docx`
        -   第三阶段 M3 Requirement Pipeline 的正式 Milestone Closure Evidence。
        -   记录 M3-U1～M3-U5、GS-01～GS-13、Aggregate Exit Gate G1～G9 已 PASS，Contract Amendment NONE。
        -   M3 Milestone Status：CLOSED。
        -   不替代 M3 Requirement Pipeline Specification 的 Contract Authority。
    -   `03_implementation-roadmap/milestones/M1/机票筛选Agent_第三阶段_M1_Architecture_Skeleton正式收口_V1.0.docx`
        -   第三阶段 M1 Architecture Skeleton 的当前正式收口基线。
        -   权威范围包括 M1 package/layer skeleton、Composition Root 边界、Architecture Guard、正反向依赖验证与 M2 入口条件。
        -   不提前定义 M2 Domain Contract、真实 Provider/LLM、SQLAlchemy ORM、Public API 业务语义或后置基础设施。
    -   例如后续形成的
        `M0-U1 Repository Bootstrap Spec`、`M3-U2 ... Spec` 等。
    -   负责定义当前 Logical Change Boundary 的
        Goal、Preconditions、Allowed Scope、Forbidden
        Scope、Implementation Contract、Fixtures、Required Tests、DoD 与
        Exit Evidence。
    -   必须遵守第 2、3 级上游基线；若需要改变稳定 Contract，应先进入
        Contract Amendment，不得由 Unit Spec 静默覆盖。
    -   在不改变上游 Contract 的前提下，当前已确认 Unit Spec 是 Codex
        本次实施最直接的执行依据。
5.  **Development Environment Readiness 文档（仅在其适用范围内）**
    -   `00_development-readiness/机票筛选Agent_第三阶段前_Development_Environment_Readiness与Codex启动指南_V0.docx`
    -   `00_development-readiness/机票筛选Agent_V0_多人协作开发环境部署与Codex复现流程_V0.docx`
    -   在 Development Environment Readiness
        阶段用于环境审计、安装/升级边界、多人复现、工具验证和 Readiness
        Gate。
    -   Readiness PASS 并进入 M0 后继续作为环境基线参考，但不得覆盖正式
        Product / Architecture Contract 或当前 Implementation Unit
        Spec。
6.  **`99_reference/` 历史与追溯资料**
    -   当前包括：
        -   `机票筛选 Agent 第二阶段工程架构设计.pdf`
        -   `机票筛选Agent_第二阶段_Phase1-19阶段性收口总结_V0.12.docx`
        -   `机票筛选Agent_V0_项目仓库结构与PowerShell初始化指南.docx`
        -   `机票筛选Agent_Phase16_技术栈选择与开发环境准备_V0.docx`
    -   仅作为历史追溯、细节查证、设计推导和辅助参考。
    -   Reference
        中的旧方案、阶段性方案、初始化示例或更细粒度建议，不得覆盖正式收口、第三阶段主控路线图或当前已确认
        Unit Spec。

## 3. 冲突处理规则

当不同文档出现冲突或表述差异时：

1.  先判断冲突属于 **Product/Domain 语义、Architecture
    Contract、实施顺序、当前 Unit Scope，还是历史参考差异**。
2.  按上述 Authority Priority
    和各文档职责边界采用更高权威、且对该问题具有直接管辖范围的文档。
3.  不得通过代码实现静默改变正式 Domain semantic、Port、Public
    API、Workflow State、Schema、Migration、Architecture Rule、Security
    Policy 或其他稳定 Contract。
4.  若正式收口与真实
    Provider、LLM、数据库、运行环境、依赖版本或其他工程事实发生冲突，停止受影响部分的实现，并输出：
    -   **Evidence**：实际发现的工程证据；
    -   **Conflict**：与哪项既有 Contract 冲突；
    -   **Impact**：受影响模块、测试、数据或兼容性；
    -   **Options**：可行方案及权衡；
    -   **Recommended Option**：建议方案，但不得自行批准；
    -   **Required Decision**：需要用户确认的 Contract Amendment。
5.  用户明确批准 Contract Amendment 后，才继续修改相关实现、测试和文档。
6.  若只是 `99_reference` 与正式收口不一致，不需要 Contract
    Amendment，直接遵循正式收口并把 Reference 视为历史材料。
7.  若当前 Unit Spec 与上游正式 Contract 冲突，应停止 Unit，而不是按照
    Unit Spec 覆盖上游 Contract。

## 4. Codex 阅读策略

Codex 不需要在每个任务中机械读取全部项目历史。原则是：

**Read minimum sufficient authoritative context.**

### 4.1 Development Environment Readiness

优先读取：

1.  `DOCUMENT_AUTHORITY.md`
2.  `00_development-readiness/` 中当前适用的环境准备文档
3.  必要时读取 `02_architecture-baseline`
    中技术栈、Deployment、Repository / Roadmap 相关部分
4.  `03_implementation-roadmap` 仅用于确认 Readiness PASS
    后的正式第三阶段入口
5.  `99_reference` 仅在需要追溯安装、技术栈或仓库结构细节时读取

### 4.2 M0--M12 Milestone 规划

优先读取：

1.  `DOCUMENT_AUTHORITY.md`
2.  `03_implementation-roadmap/机票筛选Agent_第三阶段实施与讨论大纲_V0.docx`
3.  `02_architecture-baseline` 中与当前 Milestone 相关的正式架构内容
4.  涉及产品 / Domain 语义时读取 `01_product-baseline`
5.  当前 Milestone 已形成的收口 / Unit Spec
6.  `99_reference` 仅在正式收口缺少推导细节时查阅

### 4.3 Implementation Unit 执行

优先读取：

1.  `DOCUMENT_AUTHORITY.md`
2.  当前已确认的 Implementation Unit / Milestone Spec
3.  `03_implementation-roadmap` 中该 Milestone 与 Unit 标准要求
4.  `02_architecture-baseline` 中直接相关 Contract
5.  涉及 Product / Domain 行为时读取 `01_product-baseline`
6.  仅在需要追溯时读取 `99_reference`

Codex 不应因为某份 Reference 文件包含完整 PowerShell
脚本、目录树或旧技术建议，就绕过当前 Unit Scope 直接执行。

## 5. `project-docs` 推荐结构

``` text
D:\flight-agent\
└─ project-docs\
   ├─ DOCUMENT_AUTHORITY.md
   ├─ 00_development-readiness\
   │  ├─ 机票筛选Agent_第三阶段前_Development_Environment_Readiness与Codex启动指南_V0.docx
   │  └─ 机票筛选Agent_V0_多人协作开发环境部署与Codex复现流程_V0.docx
   ├─ 01_product-baseline\
   │  └─ 机票筛选Agent_第一阶段正式收口_V0.docx
   ├─ 02_architecture-baseline\
   │  └─ 机票筛选Agent_第二阶段工程架构设计正式收口_V0.docx
   ├─ 03_implementation-roadmap\
   │  ├─ 机票筛选Agent_第三阶段实施与讨论大纲_V0.docx
   │  └─ milestones\
   │     └─ M0\
   │        └─ （后续放置 M0 收口与 M0-Ux Implementation Specs）
   └─ 99_reference\
      ├─ 机票筛选 Agent 第二阶段工程架构设计.pdf
      ├─ 机票筛选Agent_第二阶段_Phase1-19阶段性收口总结_V0.12.docx
      ├─ 机票筛选Agent_V0_项目仓库结构与PowerShell初始化指南.docx
      └─ 机票筛选Agent_Phase16_技术栈选择与开发环境准备_V0.docx
```

`milestones/` 可以在正式开始第三阶段并产生首个 Milestone / Unit
文档时再创建，不要求为了目录完整而提前创建所有 M0--M12 空目录。

## 6. Repository 与阶段边界提醒

-   `D:\flight-agent` 是项目根工作区。
-   `project-docs`
    是项目设计、治理与实施控制资料目录，不等于运行时代码目录。
-   Development Environment Readiness
    只负责环境检查、计划、经确认后的工具准备与验收。
-   在 Readiness PASS 前，不应因为 Reference
    中存在完整目录脚本而提前完成 Repository Bootstrap。
-   `git init`、项目级 `pyproject.toml` / `package.json`、正式
    repository skeleton、CI / quality baseline 等应按照 M0
    Implementation Unit 的 Scope 执行。
-   完整目标目录树是依赖边界地图，不要求一次性创建全部空目录。
-   第三阶段正式实施以 `Implementation Milestone + Implementation Unit`
    为讨论和交付单位，而不是继续沿用第二阶段 Architecture Phase
    的讨论方式。

## 7. 稳定工程原则

Codex 在第三阶段默认遵守：

-   Domain Contract 是核心稳定面；Framework、ORM、Provider SDK、LLM
    SDK、Frontend 不反向定义 Domain。
-   Domain 不依赖 FastAPI、SQLAlchemy、HTTP Client、具体 Provider SDK 或
    LLM SDK。
-   Application 依赖 Domain + Ports；Adapters 实现 Ports；bootstrap /
    Composition Root 负责组装。
-   API DTO、Domain Model、ORM Model 保持分离。
-   Mock / Fake / Fixture 是永久回归资产，不因接入真实
    LLM、Provider、PostgreSQL 而删除。
-   普通 CI 不依赖真实外部服务。
-   Secret 不进入 Repository；Generated Code 不人工修改；历史 Migration
    默认 forward-only。
-   每个 Implementation Unit 完成后 Repository 必须回到 Green baseline。
-   真实工程事实若要求改变稳定 Contract，必须走受控 Contract Amendment。
-   M9 多人并行时，不同合作者可评估不同 Provider + Acquisition Strategy 候选；每个实现 prompt 必须先执行 Git fetch、baseline、dirty-tree 与 remote synchronization 检查。其他合作者 push 后，新工程任务必须重新同步；任何单一合作者不得独立宣布最终 M9 Provider selection。

## 8. 当前阶段入口与后续流转

当前 Milestone 状态：

-   M1 Architecture Skeleton：CLOSED。
-   M2 Core Domain Contract：CLOSED。
-   M3 Requirement Pipeline：CLOSED。
-   M4 Mock Provider + Snapshot：CLOSED。
-   M5 Walking Skeleton：CLOSED。
-   M6 Complete Decision Engine：CLOSED；M6-U1～M6-U6、M6-CA01-I1、GS-01～GS-08、P0-01～P0-14 与 Aggregate Exit Gate G1～G12 全部 PASS；M6-CA01 已接纳并实现；Additional Contract Amendment NONE。
-   M7 Patch + Impact + Orchestrator：CLOSED；M7-U1～M7-U6、GS-01～GS-14 与 Aggregate Exit Gate G1～G12 全部 PASS；Contract Amendment NONE；current-main GitHub Actions CI run 33041817149 PASS。
-   M8 Real LLM / Hybrid Prototype：CLOSED；formal repository closure evidence 为 `03_implementation-roadmap/milestones/M8/机票筛选Agent_第三阶段_M8正式收口_V1.0.docx`；Closure Decision 为 `M8_RECOVERY_CLOSURE_READY_WITH_P2_GAPS`；M8-U1～M8-U7、Aggregate Exit Gate G1～G17、Hybrid Recovery RU1～RU4、final CG1～CG10、historical Parser/Patch Real LLM P0 recovery、U6H-D evidence artifact、Real-path P0 3-run、2026-08-29 unified CI 与 final unified backend 651 passed / 2 skipped 全部 PASS；P0/P1 findings none；accepted P2 gaps 为 departure-time preference、aircraft preference、airline-quality / comfort preference；M8-U6H-CA01 ACCEPTED + IMPLEMENTED + PASS；Additional Contract Amendment NONE。
-   M9 One Real Flight Provider：OPEN / IN PROGRESS；formal implementation authority 为 `03_implementation-roadmap/milestones/M9/机票筛选Agent_第三阶段_M9_Real_Shopping_Provider_Implementation_Specification_V0.1.docx`。M9-BP5-U1 已实现，commit `7a29c4f2d2bd921d423551bc9caa3556b93acb83`。当前授权下一 Unit 为 `03_implementation-roadmap/milestones/M9/机票筛选Agent_M9-BP5-U2_Fliggy_Browser_Probe_Live_Diagnosis_and_Repeatability_Specification_V0.1.docx`。M9-BP6-CA01 Lower-Bound Offer Price Semantics Proposal 已完成 human-approved governance registration，Decision Status：APPROVED，Implementation Status：NOT IMPLEMENTED。M9 closure 尚未声明；首个 Provider 尚未最终 SELECTED。

当前 effective Requirement Contract：

`M3 Requirement Pipeline Specification + M3 Closure + M6-CA01 additive amendment + M8-U6H-CA01 additive amendment`

其中 M6-CA01 仅补充：

-   `MAX_PRICE` 是 formal Hard Constraint。
-   value type 为 Money。
-   decision scope 为 OFFER。
-   `MAX_PRICE` 与 `PRICE` Soft Preference 保持分离。

其中 M8-U6H-CA01 仅补充：

-   `MAX_STOPS` 是 formal Hard Constraint。
-   value type 为 non-negative integer。
-   canonical operator 为 `AT_OR_BEFORE`，语义为 `candidate.stop_count <= max_stops`。
-   `MAX_STOPS=0` 是 Hard “必须直飞”的 canonical 表达。
-   不新增 `DIRECT_FLIGHT` Hard family；Soft direct-flight / fewer-stops preference 仍保持 Soft Ranking semantics。
-   其他 M3 semantics 保持 unchanged。

CA01 已完成治理接纳与 source implementation。M6-U6 Deterministic Relaxation + Aggregate Golden Gates 已 PASS。

M6 Complete Decision Engine 的正式 Contract Authority / Implementation
& Acceptance Baseline 由
`03_implementation-roadmap/milestones/M6/机票筛选Agent_第三阶段_M6_Complete_Decision_Engine_Specification_V1.0.docx`
定义。M6 已完成 Implementation Units、Golden Scenarios 与 Aggregate Exit
Gate，并已形成正式 Closure Authority；M6 登记为 CLOSED，M7 进入 READY /
eligible for planning and specification。

M7 Patch + Impact + Orchestrator 的正式 Contract Authority /
Implementation & Acceptance Baseline 由
`03_implementation-roadmap/milestones/M7/机票筛选Agent_第三阶段_M7_Patch_Impact_Orchestrator_Specification_V1.0.docx`
定义。M7 formal Closure Evidence 由
`03_implementation-roadmap/milestones/M7/机票筛选Agent_第三阶段_M7正式收口_V1.1.docx`
记录。M7 已完成 Implementation Units、Golden Scenarios、Aggregate Exit
Gate 与 current-main remote CI 验证，并已登记为 CLOSED；M8 进入 READY /
eligible for planning and specification。

M8 Real LLM 的正式 Contract Authority / Implementation & Acceptance Baseline
由
`03_implementation-roadmap/milestones/M8/机票筛选Agent_第三阶段_M8_Real_LLM_Specification_V1.0.docx`
定义。M8 formal repository Closure Evidence 由
`03_implementation-roadmap/milestones/M8/机票筛选Agent_第三阶段_M8正式收口_V1.0.docx`
记录；`03_implementation-roadmap/milestones/M8/机票筛选Agent_第三阶段_M8_Real_LLM正式收口_V1.0.docx`
保留为 M8 Real LLM 早期 closure evidence。M8 已完成 M8-U1～M8-U7、Hybrid Recovery RU1～RU4、M8-U6H-CA01、
historical Parser/Patch Real LLM P0 recovery、U6H-D evidence bundle、Aggregate
Exit Gate G1～G17、final CG1～CG10、Real-path P0 3-run、2026-08-29 unified CI 与 final unified backend 651 passed / 2 skipped 验证，并已登记为
CLOSED；Closure Decision 为 `M8_RECOVERY_CLOSURE_READY_WITH_P2_GAPS`；accepted P2 gaps 为 departure-time preference、aircraft preference、airline-quality / comfort preference，未来正式支持需要单独 capability / contract review，M9 不得通过 real-provider fields 静默绕过 Domain / M6 semantic boundary。M8 closure 当时使 M9 进入 READY / eligible for planning and specification；当前 M9 已由 M9 Real Shopping Provider Implementation Specification V0.1 授权进入 OPEN / IN PROGRESS，M9-BP5-U1 已实现，且 M9-BP5-U2 是当前授权下一实现 Unit。

M3、M4、M5、M6 的正式 Contract 与 Closure Authority 保持既有权威；M7
Specification 不替代 M1～M6 已稳定 Contract，也不提前授权 M8 capabilities
或 M8 implementation。

M9-BP6-CA01 Lower-Bound Offer Price Semantics Contract Amendment Proposal V0.1 已由用户明确批准进入 governance authority registration。该批准只覆盖 price semantics：Money 不变，Canonical Offer 增加 price semantics 的正式修订工作可继续；MappedOffer、Common Normalizer、Offer commercial equivalence、M6 MAX_PRICE / Ranking price-evidence safety、以及对用户暴露时的 API/UI semantic preservation 需要在后续明确授权的 implementation units 中处理。本登记不表示 CA01 已实现、已验证、已部署或 M9 已关闭。

当前首先完成：

`Development Environment Audit → Installation/Upgrade Plan → User Confirmation → Batched Setup → Verification → Development Readiness PASS`

Readiness PASS 后进入第三阶段主控路线：

`M0 Development Baseline → M1 Architecture Skeleton → ... → M12 Production Readiness`

M0 内再按已确认的 Implementation Unit 逐个实施，例如：

`M0-U1 Repository Bootstrap → Focused Verification → Exit Evidence → Green Baseline → 下一 Unit`

不得把环境准备阶段与 M0-U1 静默合并，也不得仅凭第三阶段大纲中的"建议
Units"直接让 Codex 自行扩展 Scope。具体 Unit 应先形成可验收规格。

------------------------------------------------------------------------

## Authority Rule Summary

**Latest user-confirmed instruction / Approved Contract Amendment**\
→ **Formal Product & Architecture Contract (Stage 1 + Stage 2
closure)**\
→ **Stage 3 Implementation Roadmap**\
→ **Current approved Milestone / Implementation Unit Spec**\
→ **Development Readiness documents (within their scope)**\
→ **99_reference historical materials**

当"语义权威"和"执行直接性"同时存在时：上游正式 Contract
决定"必须是什么"，当前 Unit Spec 决定"这一次具体改什么"；Unit
不得反向覆盖 Contract。
