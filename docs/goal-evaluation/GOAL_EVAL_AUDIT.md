# Goal evaluation source audit

审计日期：2026-10-17。仓库：[nano-muse/nanoMuse](https://github.com/nano-muse/nanoMuse)。固定版本：`ae9eb06b37780dd5028ab885cc38625bc7d830c0`，`main`，最新提交时间为 2026-10-16 11:14:18 +08:00。以下行号均属于此版本。

本文记录原始基线。后续已新增独立 Python 评价模块与 12 场景 Benchmark，最新实现、指标和边界见 [v1 交付报告](GOAL_EVAL_V1_REPORT.md)；原有 Goal/Scheduler 行为尚未改动。

## 结论与边界

**存在明确的 L2 切入点，但不能把 nanoMuse 看成一套统一的 Goal/Scheduler 实现。** Python 通过步骤状态计算进度，Android 直接采用模型输出的百分比；iOS 和桌面端也各有实现。它们都有执行和持久化能力，所查路径没有要求目标状态更新携带可独立验证的验收证据。

已完成源码走查、隔离评价实验和后续本机运行验证；新增文件不接入产品，不改变现有目标状态、调度器和审批。没有发布 Issue、创建 PR 或提交 commit。已验证本机 worker 在动作后突然退出的恢复对照；产品调度器、手机休眠恢复以及 L4 用户收益仍未验证。

| 能力 | 状态 | 观察依据与限制 |
|---|---|---|
| 目标持久化、步骤、周期检查 | 已实现 | 下文分别记录 Python SQLite 和 Android JSON/定时任务链路 |
| 可衡量 Goal Contract | 部分实现 | 有目标描述、步骤和部分平台的日期字段，没有结构化成功谓词、证据要求或合约版本 |
| 目标进度报告 | 已实现 | Python 步骤计数；手机/桌面模型声明。不是证据验证 |
| Trace、审计、审批 | 已实现 | 可复用动作记录和权限信息；没有自动证明结果满足目标 |
| 独立 Progress Evaluator | 缺失于所查路径 | 状态写入点未调用证据判定器，也未查询验收证明 |
| L3 恢复 | 部分实现 | 重启重设闹钟、保留会话、定时任务结果；缺少完整 Goal Run/attempt/lease 证据链 |
| 固定长期目标 benchmark | 未找到 | 已有单元/服务测试；roadmap 的公开评测方向主要是 hands，不是长期目标评估 |
| 真机和在线服务效果 | 待验证 | 本轮无真机、真实模型和外部业务服务运行 |

## Python：创建、执行、写入的调用链

```text
LLM goals.create -------------------+--> Goals.execute --> GoalStore.create --> SQLite
Web POST /api/goals ----------------+
CLI goals add ----------------------+

service._goal_scheduler --> _pick_goal_for_pass --> advance_goal --> send(MAIN_THREAD)
  --> _worker --> MuseAgent.run --> Sentinel.guard --> Goals.execute(update_step / set_status)
  --> GoalStore.update_step / set_status --> SQLite
  --> goal_to_dict --> Web Goals
```

| 环节 | 精确位置 | 已观察到的行为 |
|---|---|---|
| 组合根 | `nanomuse/app.py:82-105`、`:137-145` | Memory、GoalStore、AuditLog、Sentinel 注入 Agent；不是 Goal 的统一跨平台服务 |
| 创建工具 | `nanomuse/tools/goal_tools.py:12-25`、`:119-131` | 模型提供 title/description/steps 等；直接调用 store |
| 创建入口 | `nanomuse/server/api.py:664-678`；`nanomuse/cli.py:451` | REST 和 CLI 也可创建，不只经模型工具 |
| 存储模型 | `nanomuse/goals/store.py:102-143`、`:181-219`、`:225-267` | goals/steps 两表，兼容增列；没有 criterion、evidence、evaluation、contract revision |
| 后台执行 | `nanomuse/server/service.py:1919-1954`、`:1623-1627` | 每次选一个有 pending/in_progress 步骤的 active goal；优先逾期，再按 updated_at；忙碌、静默时段影响执行 |
| 调用 Agent | `nanomuse/server/service.py:1514-1527`、`:1242-1295` | 当前实际发送到主要聊天；不要按 architecture.md 中“独立后台线程”的概述推断 |
| 检查和执行不同 | `nanomuse/server/service.py:1591-1621` | check-in 发送提示后立即推进 next_check_in，不代表目标已经验证或任务已经完成 |
| 模型被要求更新步骤 | `nanomuse/prompts.py:258-268` | done/blocked/propose 都由执行模型根据工作判断；“不要虚构”是提示词约束 |
| 工具状态写入 | `nanomuse/tools/goal_tools.py:153-179` | update_step/set_status 无 evidence 参数或证明检查 |
| 步骤进度 | `nanomuse/goals/store.py:140-143` | `done + skipped` / 全部步骤；不按目标价值加权 |
| 自动完成 | `nanomuse/goals/store.py:480-505` | 每步为 done/skipped 时直接 set_status(done) |
| 显式完成 | `nanomuse/goals/store.py:358-365` | 校验状态枚举后写入，不要求任何步骤完成 |
| 人工/API 写入 | `nanomuse/server/api.py:684-710` | API 可直接写 status/step；未来 enforcement 不能只拦模型工具 |
| Web 展示结构 | `nanomuse/server/service.py:2690-2718` | progress 是 `{done,total}`，与 Android 的整数百分比不兼容 |
| 重新规划 | `nanomuse/goals/store.py:414-465`；`nanomuse/server/api.py:724-739` | proposal 先保存，用户接受/拒绝；可以复用而非新增第二套规划器 |
| CLI 后台入口 | `nanomuse/cli.py:321-349`；`nanomuse/app.py:501-508` | 独立于 MuseService scheduler。CLI advance 的 format 未提供 `{surfacing}`，见后面的验证限制 |

**证据 -> 发现 -> 实现机会：** `update_step` 的 done/skipped 自动完成和 `set_status` 的直接写入，说明当前进度反映任务账本；在它们旁边增加独立 evaluation 记录，可先报告“声明完成、证据未知”，不立即改变旧行为。后续强制模式必须覆盖所有写入入口和自动完成分支。

## Android：ScheduledTask 如何触发 Agent

以下 Kotlin 路径均从 `android/src/android/app/src/main/java/` 起算。

```text
io/github/nanomuse/goals/GoalFlow.startCreation
  --> SessionAddenda --> 模型 nanomuse-goal block
  --> ChatViewModel.nmAfterTurn --> GoalFlow.afterTurn --> createFromBlock
  --> GoalStore.upsert --> goals.json
  --> setUpGoal --> GoalSessions.create + ScheduledTaskManager.create(hidden, goalId)

AlarmManager --> ScheduledTaskAlarmReceiver
  --> rescheduleNext --> ScheduledAgentRunner.run(waitForCompletion=false)
  --> HeadlessChatRunner.prompt --> ChatViewModel.sendMessage --> Agent
  --> nmAfterTurn --> GoalFlow.afterTurn --> GoalStore.applyUpdate --> goals.json
  --> markFired --> ScheduledRun(ok, preview)
```

| 环节 | 位置（上述 Java 根目录下） | 关键行为 |
|---|---|---|
| Goal 数据 | `io/github/nanomuse/goals/Goal.kt:39-96` | progress Int，status ACTIVE/PAUSED/DONE；独立 sessionId/taskId，步骤没有稳定 ID |
| 创建对话协议 | `io/github/nanomuse/goals/GoalFlow.kt:42-61`、`:100-123` | 问目标、原因、检查频率；解析 fenced JSON，先存 goal，再异步设置会话和任务 |
| 创建任务 | `io/github/nanomuse/goals/GoalFlow.kt:128-154` | DAILY 或 interval，AppendToSession，hidden=true，goalId 关联。无 provider/setup 失败会留下未完整绑定的目标 |
| Memory 关系 | `io/github/nanomuse/goals/GoalSessions.kt:12-33` | 新会话继承全局 memory 开关；不代表 Memory 事实已经核验 |
| 定时接收 | `com/openminis/app/scheduled/ScheduledTaskAlarmReceiver.kt:33-99` | 先安排下一次，再后台交付；必须避免把耗时评估塞进 receiver |
| Agent 启动 | `com/openminis/app/scheduled/ScheduledAgentRunner.kt:67-142`、`:155-191` | readiness、前台服务、session 解析，10 分钟执行上限（`:35`）；结果按非 Error/Timeout 判 ok |
| 发送与等候 | `com/openminis/app/debug/HeadlessChatRunner.kt:153-241` | sendMessage，等待 streaming 结束，从会话 DB 取最后 assistant；没有 criterion-based 判定 |
| 完成回调 | `com/openminis/app/ui/chat/ChatViewModel.kt:6834-6843` | 所有会话的最后 assistant 内容交给 GoalFlow 和 FeedFlow |
| 更新解析 | `io/github/nanomuse/goals/GoalFlow.kt:63-90` | 模型给出 goal_id，或按 session fallback；解析 progress/status/note 后写入，不要求证据 |
| 生成进度 | `io/github/nanomuse/goals/GoalFlow.kt:168-188` | 系统提示要求模型输出 0-100、on_track/attention/done |
| 更新持久化 | `io/github/nanomuse/goals/GoalStore.kt:14-17`、`:32-60`、`:73-82` | JSON 整体保存，百分比 clamp；done/completed 映射 DONE，缺省分支可能把 DONE 改回 ACTIVE。不是追加式历史 |
| 人工控制 | `io/github/nanomuse/goals/GoalFlow.kt:192-218` | pause/done 会切换 task enabled；模型 applyUpdate 本身没有相同的 task disable 调用 |
| 基础恢复 | `com/openminis/app/scheduled/ScheduledTaskManager.kt:61-112` | 重启重设闹钟，完成后有限 runHistory；记录不等于持久化的 started/interrupted/reconciled 生命周期 |

**证据 -> 发现 -> 实现机会：** 模型 fenced block 应成为 claim 输入；评价器在目标会话完整回合结束、证据收集完成后生成 evaluation。不要在 block JSON 中接受模型自填的 `trusted=true`；也不要仅在 `applyUpdate` clamp 数字就称为验证。接入时绑定真实 account/goal/session/run/call，拒绝跨目标声明。当前缺少这一步，风险依据是解析器接受显式 goal_id，并非本轮已复现跨会话攻击。

## Memory、Feed、Sentinel 与其他客户端

| 关系 | 证据位置 | 对设计的影响 |
|---|---|---|
| Agent 同时注入 Memory/Goals | `nanomuse/agent/core.py:258-318` | Memory 可提供上下文，不能成为自身声明的独立佐证 |
| Memory 防虚构规则 | `nanomuse/memory/consolidate.py:166-197`；`tests/test_memory_tidy.py:183-214` | 已有“无法判断就拒绝”确定性校验先例；词汇重合不等于事实真实性，不宜直接移植为目标判定 |
| Python Feed | `nanomuse/server/service.py:2270-2299`、`:2391-2432` | Feed context 使用目标进度和 Memory；background card 来源于最后回复和产物，均非独立验收 |
| Android Feed | `android/src/android/app/src/main/java/io/github/nanomuse/feed/FeedFlow.kt:185-199`、`:278-300` | 模型 block 成为 post，digest 包含目标百分比。重复转述目标的 Feed 不能反过来证明该目标 |
| Python Sentinel | `nanomuse/agent/core.py:507-515`；`nanomuse/sentinel/gate.py:212-321`、`:362-394` | 控制工具可否执行，可复用 allowed/denied、ok、duration；授权成功和调用成功都不是目标成功 |
| 风险与模式 | `nanomuse/tools/goal_tools.py:69-96`；`nanomuse/sentinel/policy.py:113-152` | create 为 MODERATE，bookkeeping 为 SAFE；auto、规则及已有 grants 会影响审批。不能宣称现有所有目标状态变更必经人工审批 |
| Android 权限 | `android/src/android/app/src/main/java/io/github/nanomuse/guard/RiskGate.kt:20-64`；同目录 `RiskPolicy.kt:5-9`；Java 根目录下 `com/openminis/app/ui/chat/ChatViewModel.kt:9957` | 使用 Kotlin guard，非调用 Python Sentinel；也不能拿 Python 集成覆盖率代表 Android |
| iOS | `android/src/ios/NanoMuse/NanoMuseGoals.swift:192-204`、`:241-259` | 也解析 block 直接更新；done 时停 routine，与 Android applyUpdate 行为不同 |
| 桌面 | `harness/dsh-nanomuse/src/rooms.ts:1182-1203`、`:1446-1456`、`:1499-1517` | 独立 rooms store 和模型工具/fence 入口；直接采用 progress，done 设置 100。Python MVP 不会自动覆盖桌面 |

## 可复用的 Test、Trace、Eval

| 现有能力 | 证据 | 可复用部分 / 缺口 |
|---|---|---|
| 工具审计 | `nanomuse/sentinel/audit.py:13-22`、`:47-65`；`gate.py:374-393` | JSONL、时间、session、风险、允许/拒绝、耗时；output_preview 仅 300 字，其余字段还会截断。此事件没有通用 goal_id/run_id/call.id 关联 |
| Agent 上下文 | `nanomuse/sentinel/gate.py:33-48`、`:105-123`；`nanomuse/agent/core.py:427-430` | 有运行期 TaskContext，但当前 audit 不能完整重建 GoalRun，不应靠 purpose 文本模糊 join |
| 手机 trace | `nanomuse/phone/trace.py:38-115`、`:118-121` | 截图、动作、结果和时间可索引；保留上限 200 个，写入可失败，且 end.status 是操作器结果，不是独立目标验收 |
| 停滞检测 | `nanomuse/agent/core.py:383-392` | 比较最后三条 assistant 的内容与调用签名；是单次会话重复，不是跨天目标无进展 |
| Store 测试 | `tests/test_memory_goals.py:143-180`、`:364-478` | 生命周期、proposals、cadence、旧 DB 升级；当前测试明确预期自动完成 |
| 服务测试 | `tests/test_server.py:783-961`、`:1569-1700` | Goal API、Feed、quiet hours，可复用 MockLLM + TestClient；尚不检查 goal evidence |
| 桌面协议测试 | `harness/dsh-nanomuse/tests/fences.test.mjs:10-50`；`rooms.test.mjs:19-24` | parser 限制、百分比 clamp、标题；不构成长期目标 benchmark |
| 公开评测方向 | `docs/roadmap.md:37-43`、`:61-65` | 明确提出固定 hands 任务评测集、Memory provenance；适合作为邻接方向，不能称 GoalEval 已实现 |

## GitHub 设计检索与贡献规范

2026-10-17 用只读 `gh api 'repos/nano-muse/nanoMuse/issues?state=all&per_page=100' --paginate` 检索了 489 条条目，其中 52 个 Issue、437 个 PR；检查 title/body，关键词包含 goal、progress、evaluation、benchmark、evidence、目标、评估。没有找到专门提出 evidence-based goal evaluation/Goal Contract 的条目。**这是此时的标题和正文检索结果，未覆盖全部评论、Discussion、未推送分支或 maintainer 私下计划。**

- [PR #170](https://github.com/nano-muse/nanoMuse/pull/170) 涉及 Goals 页面模块与测试，属于前端拆分，不是目标评价。
- [Issue #270](https://github.com/nano-muse/nanoMuse/issues/270) 描述 Memory guard 无法判断时误放行的问题；当前固定源码已有 guard 和对应测试。Issue open 不代表代码尚未修复。
- ScheduledAgentRunner 注释中的 GH 编号不能直接当本仓库 Issue 证据。例如本仓库 [#197](https://github.com/nano-muse/nanoMuse/pull/197) 实际是脚本修复，和代码注释所指主题不同；本文以代码行为作为依据。

可复现检索：先把上述 API 输出存至临时目录，再解析 JSON 中 title/body；PR 通过 `pull_request` 字段识别，不把它们重复计为 Issue。需提案时重新检查上游最新状态。

贡献边界来自 `CONTRIBUTING.md` 开头及 *Issues and pull requests (maintainers)*，以及 `.github/ISSUE_TEMPLATE/feature_request.yml`：大功能先讨论 Issue；Feature 草稿遵循 problem/proposal/alternatives/area；建议 enhancement + runtime 标签。未来 commit 用 Conventional Commits 和 DCO sign-off。没有 CODEOWNERS 文件；根 AGENTS.md 是唯一版本化的 AGENTS.md。

## 本轮验证及未验证项

隔离实验见 [测试计划](GOAL_EVAL_TESTPLAN.md)。其中两个 characterization test 直接加载当前 `nanomuse/goals/store.py`，验证无需证据即可 all-skipped -> done、pending steps -> 显式 done。它们证明当前行为，不是对产品的修改。

原型实际读取临时文件、独立计算 SHA-256，再对修改后的文件重新观察；外部业务条件使用受控 fixture，不能算在线事实校验。SQLite 重开和回滚测试证明实验记录可恢复与同一 attempt 不重复落库，不能证明真实 Agent 进程死亡后的动作 exactly-once。

后续新增 [本机失败模式验证](GOAL_EVAL_FAILURE_MODES.md)：在原有 MuseAgent/Sentinel/Files/Goals 链路上执行 9 个受控场景，以真实 loopback 服务独立检查结果，并注入子进程突然退出。已得到“盲目重试 2 次提交、先 reconcile 保持 1 次提交”的对照；这是实验策略，不是现有 scheduler 的能力声明。新增 11 项测试，与原型共 28 项通过，保存 23 个可重放快照。

另发现 CLI `NanoMuseApp.advance_goal` 使用的 prompt 包含 `{surfacing}`，而该入口只传 goal（`nanomuse/app.py:508` 对比 `nanomuse/prompts.py:268`）；本轮直接执行相同 format 表达式已复现 `KeyError('surfacing')`，未运行真实 CLI/模型，不混入 feature 修改。

相关原有测试在临时 Python 3.12.9 环境中安装 `.[dev]` 后执行。首轮受继承的 SOCKS 代理影响，缺少 socksio 导致 2 failed、59 passed、1 skipped、55 errors；仅清除测试进程的 HTTP/HTTPS/ALL_PROXY 及小写同名变量后重跑，结果 **116 passed、1 skipped，11.07 秒**。不以首轮环境失败推断产品功能缺陷，未修改依赖文件。隔离实验 **17 tests passed**，含 15 项评价/持久化测试及 2 项原代码行为测试；Ruff lint/format 和原型 mypy 通过。完整命令见测试计划。

建议首个实现 PR 仅提供 opt-in contract/evaluator/ledger 和离线测试，然后做 Python shadow 集成；Android 另做 adapter 与真机恢复测试。详细边界见 [RFC](GOAL_EVAL_RFC.md)，对外文本见 [英文 Issue 草稿](GOAL_EVAL_ISSUE_DRAFT.md)。
