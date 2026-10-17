# Goal evaluation test plan

日期：2026-10-17。固定源码版本：`ae9eb06b37780dd5028ab885cc38625bc7d830c0`。配套 [审计](GOAL_EVAL_AUDIT.md)、[RFC](GOAL_EVAL_RFC.md)。本文件区分已自动运行的实验、现有回归和尚未执行的产品验收。

最新 v1 已固定 [12 场景数据集](GOAL_EVAL_DATASET.md)，两轮结果与可重放的逐场景报告见 [交付报告](GOAL_EVAL_V1_REPORT.md)。当前全部实验为 49 项测试（25 核心、18 运行、6 指标）；本文件下方的 17/28 项与 9 场景是早期验证记录，保留演进依据，不再作为当前总数。

## 运行已交付的验证

在仓库根目录执行，只需 Python 标准库，不启动 nanoMuse、不读个人配置、不访问网络：

```bash
python3 -m unittest discover -s experiments/goal_eval -p test_prototype.py -v
```

初始结果：**17 tests passed**，15 项评价/持久化实验测试、2 项直接使用现有 GoalStore 的 characterization tests。v1 补充独立审查反例后，该命令当前执行 **25 项核心测试**。多个边界输入通过 subTest 运行，不把每个断言包装成独立 benchmark 任务。

源文件：[prototype.py](../../experiments/goal_eval/prototype.py)、[test_prototype.py](../../experiments/goal_eval/test_prototype.py)。所有时钟固定为 `NOW=10000`，时长单位为秒；测试用临时目录，结束自动删除数据库和文件。外部服务状态为合成 fixture，只有文件 collector 实际读盘。

后续已补真实 Agent 工具链的本机验证，见 [失败模式与 evaluation 体系](GOAL_EVAL_FAILURE_MODES.md)：9 个场景、11 项新增集成测试，连同本节的 17 项共 **28 tests passed**。本机场景实际启动 loopback HTTP 服务，执行工具，注入子进程突然退出，并保存可重放快照；模型仍是脚本化 MockLLM。上面的 `-p test_prototype.py` 保持标准库独立可跑；执行全部实验需安装仓库依赖。

## 五个必要场景

共同契约为目标 `g_1`、revision=1，必须有至少一个 outcome 条件；证据绑定逻辑 run 和 call。证据由可信测试 harness 提供，不接受模型输入的 collector 身份。基础 predicate 是外部对象 `fixture://submission/1` 的 accepted==true，unit=boolean，TTL=60 秒。

| 场景 | 输入与动作结果 | 证据 | 期望评估与决策 | 自动断言所在方法 |
|---|---|---|---|---|
| 1. 任务完成且目标推进 | run completed；有可比较 baseline accepted=false | 同合约、对象、单位的新观察 accepted=true | verified；delta=+1；先 escalate(review_completion)，匹配评价 ID 的测试审批后 stop | `test_task_completed_with_real_progress` |
| 2. 任务成功但无目标进展 | run completed，例如重做准备工作；前后 accepted=false | 服务能确认仍未 accepted | partial；met=[]，delta=0。连续 3 个不同逻辑 run 无进展才 replan；本实验直接传入计数验证决策 | `test_task_succeeded_without_goal_progress` |
| 3. 模型声称完成、无证据 | 模型“已完成”，其声明记录 collector=llm.self_report | 不属于允许的独立来源，或证据为空 | unknown；delta=null；continue(collect_evidence)，3 个未知逻辑 run 后 escalate | `test_model_claim_without_evidence` |
| 4. 外部变化使计划失效 | 合约增加必要 invariant：window_open；原计划依赖该窗口 | 可信 fixture 确认窗口已关闭；outcome 尚无证据 | contradicted；violated 包含 window；replan 只是建议，不替换步骤 | `test_external_condition_invalidates_plan` |
| 5. 中断、重试、恢复 | attempt=1 interrupted；关库后重开，attempt=2 completed | attempt=1 无可用证明，attempt=2 观察 accepted=true | 先 unknown/reconcile_before_retry，恢复后 verified；重放同 attempt 返回相同结果，库中仍只有两条评价 | `test_interrupted_retry_and_reopen_do_not_double_count` |

场景 1 的 delta 证明受控状态改变，不证明一般业务中的因果贡献；场景 2 的 partial 可为零进展；场景 3 禁止把缺失事实记成已知失败；场景 5 是本地 ledger 恢复测试，未模拟 Android OS kill 或真实有副作用的动作恢复。

## 边界、反例和可追溯性

| 方法 | 断言 |
|---|---|
| `test_real_artifact_recheck_detects_tampering` | 真实临时文件 hash 满足锁定值时 verified；改写文件并独立重读后 contradicted，不能靠旧 hash 保持完成 |
| `test_unusable_evidence_never_verifies` | 错 goal、revision、run、subject、metric、call 缺失、单位错误、TTL=61 秒、未来观察/采集、tool failed、denied、整数/字符串冒充 boolean 都 unknown |
| `test_digest_mismatch_is_unknown` | 改 normalized value 而不更新摘要，证据拒绝，不得判 met |
| `test_duplicate_and_conflicting_ids` | 同证据重复只计一次；相同 ID 携带不同内容不能选择其中有利的一条 |
| `test_conflicting_independent_observations_require_review` | 同一最新观察时间出现 true/false，criterion=conflict，整体 contradicted |
| `test_latest_valid_observation_wins` | 较新有效观察覆盖旧观察；排列输入顺序不影响纯评价结果 |
| `test_partial_coverage_and_no_vacuous_success` | 两项仅一项有证据时 partial；delta=null；空 criteria 和仅 constraints 的合约拒绝 |
| `test_revision_baseline_and_nan_are_rejected` | 跨版本 baseline、未来 baseline、NaN target 都抛 ValueError，不能生成可用成功结果 |
| `test_numeric_predicate_and_units` | count>=3：2 为 partial、3 为 verified；True 不得当数字 1 接受 |
| `test_sqlite_transaction_rollback` | 事务内中途 SQL 错误，整次写入回滚，不能留下半条已提交记录 |
| `test_skipping_all_steps_completes_upstream_goal_without_evidence` | 当前真实 GoalStore 两步 skipped -> progress=2/2、status=done、notes 为空，证明现有缺口 |
| `test_explicit_done_needs_no_finished_steps` | 当前真实 GoalStore 一个 pending step 仍能 set_status(done)，progress=0/1 |

实验中的 Evidence 保存 goal/revision/run/call、criterion、collector、subject、metric/value/unit、observed_at/collected_at、allowed/ok 和快照摘要。Evaluation 保存采用/拒绝的证据 ID、criteria、delta、violated/lost，输入与结果一起写入 SQLite，可复核“为何得出该结果”。source 身份认证、完整原始 payload 及审批鉴权不在本实验中。

## 已执行的原有回归与环境

临时 venv 使用 Python 3.12.9，未改仓库依赖文件。可重新创建自己的临时环境：

```bash
uv venv /tmp/nanomuse-goal-eval-venv
uv pip install --python /tmp/nanomuse-goal-eval-venv/bin/python -e '.[dev]'
env -u ALL_PROXY -u all_proxy -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy \
  /tmp/nanomuse-goal-eval-venv/bin/python -m pytest -q \
  tests/test_memory_goals.py tests/test_memory_tidy.py tests/test_sentinel.py tests/test_server.py \
  -m 'not live' --tb=short
/tmp/nanomuse-goal-eval-venv/bin/ruff check experiments/goal_eval
/tmp/nanomuse-goal-eval-venv/bin/ruff format --check experiments/goal_eval
/tmp/nanomuse-goal-eval-venv/bin/mypy experiments/goal_eval/prototype.py --follow-imports=skip
```

已观察：原有回归 **116 passed、1 skipped，11.07 秒**；隔离代码 lint/format/type check 通过。首轮继承本机 SOCKS 代理，因 socksio 缺失得到 2 failed、59 passed、1 skipped、55 errors，清除该测试进程的代理环境后解决。跳过的是 `tests/test_server.py:2107-2108` 的 browser tool 测试，skip 条件为 Playwright/Chromium 未安装，不把它称为通过。以上依赖没有 lock，未来新解析版本可能影响结果。

另运行 `python -m pytest -q tests/test_docs_markup.py`，首轮 **87 passed**；加入失败模式文档后为 **88 passed**，包含本轮五份文档。相对文档链接另做本地存在性检查；未构建完整 VitePress 网站。

CLI 补充复现：

```bash
/tmp/nanomuse-goal-eval-venv/bin/python - <<'PY'
from nanomuse import prompts
try:
    prompts.ADVANCE_GOAL_PROMPT.format(goal='fixture goal')
except KeyError as exc:
    assert exc.args == ('surfacing',)
    print("Reproduced: missing surfacing in the CLI formatting path")
else:
    raise AssertionError('Baseline changed; recheck nanomuse/app.py:508')
PY
```

这是相同格式化表达式的复现，不是完整 CLI E2E。未运行全仓库测试、Android/iOS/桌面构建、真机、生产 connector 或在线模型测试。

## 首个小 PR 的验收门槛

| 指标 | 计算口径 | 门槛 |
|---|---|---|
| 无依据完成 | 标注为无证据/不可用证据的固定样本里，输出 verified 的比例 | 0；任一反例阻塞 |
| 条件覆盖 | verified outcome 条件集合，不按工具调用次数计分 | 与 fixture ground truth 完全一致 |
| unknown 正确性 | 完全没有可判断证据的固定样本 | 全部 unknown，delta=null |
| 版本/重复隔离 | 跨 goal/revision/run 与相同 attempt 重放 | 不串证据，不重复计入；changed input 拒绝 |
| 重放一致性 | 相同 contract、证据快照、clock、evaluator_version | 输出和结果 ID 一致 |
| 解释完整性 | 每项评价和采用/拒绝证据关联 | 可找到 criterion、evidence ID 及判定依据 |
| 回归 | 现有 Goal、Memory、Sentinel、Server MockLLM tests | 通过；环境问题单独记录 |

这些是设计正确性的固定样本门槛，不是统计意义上的真实成功率。当前已通过本轮实验和局部回归，不声称上表的产品集成条件已经全满足。

## 集成后必须补的验收

以下是产品集成验收门槛。本机 harness 已验证其中的服务 503/关闭窗口、审批拒绝、服务结果撤销和“动作后突然退出再恢复”子集；其余尚未实现或执行。它们是启用 enforcement / 宣称 L3 可靠性之前的条件：

1. Python MockLLM 发出 update_step(done)、set_status(done)，但 checker 缺失；shadow 保留旧行为并显示 unknown，enforced goal 拒绝无证明的自动完成。API、全 skipped、人工 override 均覆盖，override 标注 user_attested。
2. 真实受控 HTTP 服务响应 accepted/pending/closed、超时、401、畸形 payload、响应内 prompt injection；仅固定 subject/metric 的合法事实可用，不能通过状态码 200 升级成功。
3. 同一 scheduler occurrence 重复触发、两个 checkNow 并发、重启后旧回调到达，确保逻辑 run 唯一；对 held approval 不重复发起敏感动作。
4. 在动作前、动作后但证据落库前、evaluation 写入前、状态写入与 Feed 通知之间分别 kill worker；重启先 reconcile，不能双发消息/付款；outbox 只推送一次。
5. 评价期间用户改目标、切账号、撤销 approval 或证据过期，旧结果只能保留为历史/stale，不推进新目标状态。
6. Android 真机验证 receiver 非阻塞、Doze、reboot、无 provider、网络掉线、暂停/完成后不再触发；保持现有 10 分钟 runner 上限和 receiver handoff 时限。需记录设备、OS 和实际恢复延迟。
7. 相同 JSON fixtures 在 Python/Kotlin/TypeScript 的类型、TTL、conflict、unknown、revision 和排序语义一致，再考虑多平台覆盖。

L3 benchmark 建议先用虚拟时钟的 30 个逻辑日、每日至少一次检查，固定故障注入种子，统计 valid_progress_runs、unknown_runs、zero_delta_runs、regressions、recovery_latency、duplicate_side_effects 和 approval_wait。先建立受控环境 ground truth，再做真实长期任务；不要用测试次数、执行时长或模型自报百分比替代价值指标。
