# RFC: Evidence-based goal evaluation

状态：提案与隔离原型，未接入产品。依据版本：`ae9eb06b37780dd5028ab885cc38625bc7d830c0`。现状和源码定位见 [审计](GOAL_EVAL_AUDIT.md)。

v1 已落地为 [nanomuse/goals/evaluation.py](../../nanomuse/goals/evaluation.py) 中可独立调用的模块，包含严格 Contract 解析、attempt 绑定、确定性评价、Ledger 和决策建议。实验入口保留兼容导入；没有注册运行 hook。12 场景、两轮实测和未实现项见 [交付报告](GOAL_EVAL_V1_REPORT.md)。下文跨端、审批、shadow/enforcement 内容仍属于后续设计。

## 建议的最小功能

先让 nanoMuse 对一个显式验收条件回答“当前有何证据、能否确认、与上次相比变了什么”。首个切片是 **L2 的确定性评估 + L3 的运行关联记录**，采用 shadow 模式。保留旧 progress，增加独立 evaluation；不承诺统一所有客户端，不先造通用 AgentEval 框架。

```text
用户确认 Goal Contract(revision)
  --> Agent Execution(run_id, attempt, call_id)
  --> Evidence Collection(只读、独立来源、受权限控制)
  --> Goal Evaluation(确定性聚合 + 可选辅助判断)
  --> Decision recommendation
       +--> continue: 下一步 / 采集缺失证据
       +--> replan: 产生 proposal，等待用户接受
       +--> escalate: 展示未知、冲突、审批或预算耗尽
       +--> stop: 已批准完成或用户取消后，由调度控制器执行
```

这是建议流程。当前已观察到的产品流程在审计中单独给出；上述箭头不表示现有源码已经实现这些节点。

## 四层边界

| 层级 | 核心问题 | 第一阶段 |
|---|---|---|
| L1 Task | 调用/单次任务是否正确 | 复用 ToolResult、Sentinel、task trace，保留 task_ok；不能代替目标判定 |
| L2 Progress | 哪个验收条件获得了新证据 | 本轮核心：条件状态、证据引用、可比较快照差量 |
| L3 Continuity | 中断、重复、等待后能否继续推进 | 先记录 run/attempt、缺失证据、重试和停滞；真实后台恢复后续集成验收 |
| L4 Outcome | 用户最终目标是否达到 | 只有全部必要条件满足且完成确认有效才建议完成；长期用户收益不由本次单元测试证明 |

## 数据结构与所有权

以下是拟集成 schema，**不是当前产品接口**。隔离原型实现其可执行子集，字段映射见后文。ID 和来源可信度由宿主产生，Agent 只能声明或引用已存在的证据。

| 记录 | 必要字段 | 写入者与约束 |
|---|---|---|
| GoalContract | schema_version, account_scope, goal_id, revision, goal_snapshot_hash, objective, criteria[], constraints[], approved_by, approved_at, budgets | 用户确认后宿主持久化；目标/条件变化产生新 revision，不能模型自己降低阈值 |
| Criterion | id, subject, metric, op, target, unit, source_policy, max_age_seconds, required, completion_role | MVP 全部 required，仅 eq/gte；至少一个 outcome 条件。constraints 是必须保持的 invariant，不应单独构成成功 |
| GoalRun | run_id, goal_id, contract_revision, session_id, schedule_id?, scheduled_for?, attempt, state, started_at, ended_at?, retry_of? | 宿主在执行前写 started，结束/失败时更新；进程死亡由 lease/reconciliation 判 interrupted，不靠 LLM 自报 |
| Evidence | evidence_id, account_scope, goal_id, revision, run_id, action_id/call_id, criterion_id, collector_id/version, source_uri, observed_at, collected_at, payload_ref, payload_hash, normalized_fact, permission_decision, collection_status, provenance_class | 可信 collector 写入。事实可以由只读 recheck 获得，不要求每条证据来自执行模型的工具输出 |
| EvaluationResult | evaluation_id, schema_version, evaluator_version, contract_hash, run/attempt, evaluated_at, criterion_results[], verdict, verified_criteria, required_criteria, delta?, evidence_ids[], rejected_evidence/reason, stale, recommendation | 评价器只读输入，追加结果；保存完整 canonical snapshot 才能重放 |
| DecisionProposal | id, evaluation_id, action, reason_code, missing_evidence[], proposed_steps?, approval_requirement, expires_at | 只是建议。应用变更时检查目标版本、最新 evaluation 和用户审批，记录实际 applied 或 rejected |

`source_uri` 是定位信息，不是可信度。`payload_hash` 只能检查快照是否变化，不能证明外部事实为真。`provenance_class` 建议使用 host_observed / external_observed / user_attested / agent_claim，附 collector/version；避免模型自填 0.95 confidence 伪装可靠度。

Memory、Feed、assistant summary 和来自同一模型的“裁判”默认属于 claim/上下文。它们可以帮助定位待检查对象，不能因互相引用升级为独立证据。用户自述可作为 user_attested，只有 contract 明确接受此类证明时才计入，并在 UI 保留来源类别。

### 一个有明确验收边界的例子

目标：“生成一份满足指定格式的周报，并让指定接收服务接受它”。两项 outcome 条件：文件通过固定 schema 检查、外部服务中指定对象状态为 accepted。一个 invariant：提交窗口仍开放。合约预先锁定文件/服务对象标识和版本，发送行为继续经过现有审批。

```json
{
  "schema_version": 1,
  "goal_id": "g_example",
  "revision": 1,
  "criteria": [
    {"id": "report_valid", "subject": "artifact://weekly-report", "metric": "schema_valid", "op": "eq", "target": true, "unit": "boolean", "collector": "report.schema.v1", "max_age_seconds": 3600},
    {"id": "accepted", "subject": "fixture://submission/1", "metric": "accepted", "op": "eq", "target": true, "unit": "boolean", "collector": "fixture.external.v1", "max_age_seconds": 60}
  ],
  "constraints": [
    {"id": "window_open", "subject": "fixture://submission-window", "metric": "open", "op": "eq", "target": true, "unit": "boolean"}
  ]
}
```

这是数据示例；`fixture://` 明确表示测试服务，不代表真实 connector 已实现。先做受限谓词，不接受用户/模型提供的可执行 Python、shell、SQL 或任意 URL 检查脚本。

## 确定性、事实校验和 LLM 的职责

| 路径 | 做什么 | 无法判断时 |
|---|---|---|
| 确定性规则 | 字段/类型/单位校验、范围比较、JSON schema、文件摘要、关联/时间/版本校验、冲突和聚合 | unknown 或拒绝非法合约，不能让空 criteria 得到 verified |
| 外部事实校验 | 按合约固定对象和只读 adapter 查询业务状态，保存响应快照/版本/ETag，处理超时、撤销和权限拒绝 | unknown；HTTP 200、工具 ok、页面标题不等于目标 predicate 满足 |
| LLM 辅助 | 从文本中提出候选条件、识别需核实的断言、解释结果、草拟 proposal；复杂文本 rubric 可输出辅助判断 | 输出 claim/needs_review，保留模型版本与引用；首版不允许单靠它授予 verified |

采集器通过原平台的 Sentinel/guard 获取访问权限；只读访问也可能涉及私人数据。宿主接收已授权结果，脱敏后保存最小必要快照。检查器错误或外部内容中的指令不得改写 contract/决策规则。原型仅有实际本地文件 hash collector 和受控外部 fixture，没有生产 HTTP adapter。

## 判定语义

条件内部状态为 `met / not_met / unknown / conflict`，避免把“确实没达到”与“无法判断”混在一起。每个状态包含 evidence_ids 和 reason_code。

| Verdict | 定义 | 例子 |
|---|---|---|
| verified | 全部必要 outcome 与 constraints 都有有效证据满足 | 文件有效且服务 accepted，窗口有效 |
| partial | 有条件能判定，整体尚未全部满足；允许 verified_count 为 0 | 服务确认仍 pending，这是已知未达标而非不可知 |
| unknown | 所有必要条件都缺乏可用证据，或尚无用户确认的可评估合约 | 只有“我做完了”的模型声明；服务不可访问 |
| contradicted | 有 invariant 失效、曾确认的条件被新证据否定，或当前证据冲突 | 报名关闭、文件被替换、同一观察时间出现矛盾记录 |

优先级：contradicted > 全 met 的 verified > 全 unknown 的 unknown > partial。`partial` 不表示已有正进展。错误、超时、过期、来源不接受、跨目标/版本/动作的证据应被列为 rejected 并使相关条件 unknown，不能自动判 not_met。

有效性顺序：校验 account/goal/session/run/action 关联 -> 检查来源策略与权限 -> 检查完整性和字段类型/单位 -> 检查 observed_at/collected_at/TTL -> 针对同一 subject/metric 选当前观察 -> 执行 predicate -> 聚合。只有 contract 明确允许时才使用历史证据，并重新检查有效期。原型更保守，只接收当前 run 的 observation。

较新的有效观察可以取代旧观察。同一观察时间的有效来源若值不同，先判 conflict，不能挑有利样本。宿主须校正时间来源；无法确认外部时钟/版本顺序时返回 unknown，不能仅信任网页自填时间。

### “满足条件”与“本次推进”的区别

MVP 展示已验证条件集合，不生成主观目标百分比。`verified_count/required_count` 是覆盖度，不是用户价值百分比；invariant 与 outcome 可在集成后分别计数。

只有相同 contract hash、按时序排列且前后条件状态都可判定的快照才计算整数 delta，且只计算 outcome 条件数量变化；约束满足不算正向成果。否则 delta=null，并列出当前已知条件和缺口。首次观察只能证明当前状态，不能证明 Agent 造成了变化。受控前后快照可以证明状态变化；因果归因还需要动作与外部对象关联，不能把外部自然变化记成 Agent 的独占贡献。

重试和相同事件不累加覆盖度；按 criterion ID 集合比较。条件回退记 lost/regression，不强制进度单调。合约重规划换 revision 后重建 baseline，保留旧评价历史，不让分母改变制造“进度提升”。

## 决策接口及人工控制

```text
evaluate(contract_snapshot, run, evidence_snapshot, previous?, clock) -> EvaluationResult
recommend(result, continuity_counters, budgets) -> DecisionProposal
apply(proposal, current_goal_version, approval_receipt?) -> AppliedDecision
```

evaluate/recommend 不执行外部行动。apply 属于宿主控制平面，模型无权自行调用受信的审批写入接口。

| 条件 | 建议 | 执行限制 |
|---|---|---|
| partial 且没有持续停滞 | continue | 执行仍经过 Sentinel；已知未达标才计入无进展 |
| unknown，仍有验证预算 | continue / collect_evidence | 不推进 verified progress，不因未知盲目重做有副作用的动作 |
| 连续 3 个不同逻辑 run 无法验证 | escalate | 3 是提案默认值，可配置；重试同一个 run 不增加计数 |
| 连续 3 个不同逻辑 run 在可比较快照下 delta=0 | replan | 生成既有 proposal，用户接受前不替换计划 |
| contradicted | replan | 暂停依赖失效条件的行动；冲突无法消解时再 escalate |
| verified | escalate / completion_review | 保留等待用户确认；不能靠模型状态块把合约标为已批准 |
| 用户批准最新、仍有效的 verified 评价 | stop | CAS 更新目标并禁用对应 schedule；保存审批及应用记录 |
| 用户取消 / 硬预算耗尽 | stop / budget_hold | 取消与成功分开，预算停止不能标 done；保持可恢复的暂停状态 |

完成审批绑定 account、goal、contract revision、evaluation_id、有效期；旧评价、过期证据或修改后的目标不得沿用批准。contract 编辑、计划替换、完成确认保留独立用户控制；敏感外部动作继续使用原审批能力。此处是新增产品语义，不能宣称 SAFE bookkeeping 已经提供这种保证。

## L3 基础可观测性与恢复方案

逻辑 run 按一次计划执行定义，attempt 表示同一次运行的重试。调度器写入唯一 `(goal, revision, schedule_occurrence)`，运行记录关联 `run_id/attempt`；工具调用和只读 checker 均有 action ID。先落 started，再调用 Agent。重启发现过期 lease 时标 interrupted，先读回外部对象确认副作用是否已发生，再决定 retry。

计数包括 logical_runs、attempts、interrupted、unknown、zero_delta、regressions、恢复延迟、审批等待时间和 evaluator 耗时。审批等待与服务中断分别记录，不能统称 Agent 无效。单调时钟用于持续时间，UTC 用于可追溯观察；测试注入固定时钟，不真实 sleep。

纯原型证明的是 SQLite 输入/评价事务和 reopen 后幂等。后续 [本机 harness](GOAL_EVAL_FAILURE_MODES.md) 另验证了提交工具成功后子进程突然退出，以及两种恢复策略的副作用差异；没有覆盖产品 scheduler、lease、手机 OS 生命周期或一般对外动作 exactly-once。集成阶段需要同一事务保存状态与待通知 outbox，并以幂等 key 推送 Feed；无法事务化的外部动作必须 reconcile，而不是宣称 SQLite 能解决分布式 exactly-once。

## 接入与兼容性

| 阶段 | 拟改位置 | 范围 |
|---|---|---|
| 小 PR 1 | 新增 `nanomuse/goals/evaluation.py`、独立评价 store 与 tests | 标量谓词、schema 校验、canonical snapshots、纯评价、幂等，默认关闭；不改 Goals 行为 |
| 小 PR 2 | `agent/core.py`、`sentinel/gate.py`、`server/service.py` 的窄 hook | 显式 GoalRun 上下文和完整脱敏证据引用；捕获 denied/error/timeout；Python shadow，对比 claim 与 evaluation |
| 小 PR 3 | `goal_tools.py`、`goals/store.py`、`server/api.py`、Web 展示 | 用户 opt-in enforcement；处理自动 done、set_status、API 手动覆盖，标注 user_attested，增加审批；更新文档、locale 和 Changelog |
| 后续 | Android 新 `io.github.nanomuse.goals.evaluation` 文件与完成回调 adapter | 先做同一组 JSON fixture 一致性；不要求 Kotlin 调 Python。iOS/desktop 按需适配 |

拟集成存储采用版本化 side tables 或单独 `goal-evaluations.db`，不删除现有字段；具体选择由 maintainer 定。旧 goals 没有 contract -> evaluation unknown；旧客户端忽略新增可选字段，原 progress wire shape 不变，遵循仓库一版兼容要求。contract/evidence 的账户归属、导出、删除与现有 account data 生命周期共同设计，不能把一个账号的证明带给另一个账号。

shadow 的含义：**仍可能看到旧系统标 done**，旁边明确“执行者声明完成，证据未知”；不得把 shadow 包装成防止所有虚报的完成方案。只有 opt-in enforcement 并覆盖全部状态写入点后才能声称阻止无证据完成。

集成代码不能把耗时 evaluation 放进 Android BroadcastReceiver；应在回合完成后交由后台 observer，并处理 evaluation 尚未完成的中间态。重复 nmAfterTurn、并行 checkNow、用户同时编辑目标时，使用 contract snapshot hash 与版本 CAS，旧结果保存为 stale，不更新当前 verified progress。

快照按最小字段保存，沿用 vault redaction，不保存凭据；私有 raw payload 访问受原权限控制。定义保留期、用户删除和 trace prune 后的引用行为：payload 已删除则新重放 unknown，旧结论保留其历史时间和不可重放标记。hash 不承诺篡改防护，若未来需要强审计再考虑签名/不可变存储。

## 本轮原型与拟集成方案的差异

纯评价文件：[prototype.py](../../experiments/goal_eval/prototype.py)、[test_prototype.py](../../experiments/goal_eval/test_prototype.py)。仅标准库，无网络、无模型、无工具注册。Contract/Criterion、Evidence、evaluate、decide、Ledger 是可执行子集。后续单独增加 [local_harness.py](../../experiments/goal_eval/local_harness.py)，使用仓库原有依赖、MockLLM、真实工具和本机 HTTP 服务，不引入生产 hook。

已验证：eq/gte、单位与类型、绑定/时效/摘要检查、四类 verdict、差量/回退、重复和冲突证据、实际文件重查、SQLite reopen/rollback 和相同 attempt 输入冲突拒绝。

尚未实现：用户合约确认、account/session 权限认证、producer 身份认证、真实外部 connector、持久化 continuity counters、真实审批、通知 outbox、并发 writer 协调、目标版本 CAS、调度恢复。outcome/invariant 已分开计数，delta 仅计算成果变化。原型的 `collector` 字符串与 `approved_result_id` 仅由受控 harness 提供，**绝不可原样暴露为 Agent 可提交的信任标记或授权接口**。SQLite 单 writer，不是生产多设备同步设计。

不纳入第一阶段：奖励模型、另一个 LLM 自动裁决所有目标、任意代码 predicate、通用排行榜、全平台重构。v1 先合入贡献者自己的 fork 作为协作基线；向官方上游提案前仍需确认 Python 首发与 shadow 边界。后续分工见 [协作入口](GOAL_EVAL_NEXT.md)。
