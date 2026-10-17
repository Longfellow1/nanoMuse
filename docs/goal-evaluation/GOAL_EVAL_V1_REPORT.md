# GoalEval v1：本机评测交付报告

第一版已交付可独立调用的 Python 评价核心、机器可读合约 Schema、12 个有标准答案的场景及独立评分器。两轮本机实跑得到相同的语义结果与全部指标。当前属于 P1 和运行链路验证，尚未把评价接入产品状态控制。

## 产品结论

固定集里有 8 个实际未达标场景，原有目标账本其中 7 个记为 done；新评价器没有把这 8 个场景中的任何一个核验为成功。3 个证据完整的正向对照均能被确认，2 个无法核验的场景均保留 unknown。

这证明第一版可以在受控条件下识别无依据的完成宣告，同时支持正常成功；没有证明真实模型出错率下降，也没有改变产品原有 done 行为。

## 评测设计与复现边界

- 源码基线：`ae9eb06b37780dd5028ab885cc38625bc7d830c0`；新增模块的精确 SHA-256 保存在每轮 `environment.json`。
- 数据集：`false-completion.v1`；12 个场景预先指定 verdict、决策、提交次数、源状态和成果真值，见 [评测集设计](GOAL_EVAL_DATASET.md)。
- 模型：仓库 MockLLM 固定工具调用。原有 Agent、Files、Goals、Sentinel 实际执行；文件、SQLite、本机 HTTP 和子进程故障都真实发生。
- 运行环境：Python 3.12.9；两轮输出分别为 `.git/goal-eval/v1-run-01/`、`.git/goal-eval/v1-run-02/`，生成数据不加入 Git。
- 真值通路：独立评分器直接检查控制器的服务计数、产物与 GoalStore，不以执行者文字或 Evaluator 的 verdict 作为成果真值。
- 评价版本：`goal-eval.v1`。此前的 `experiment.v1` 报告保留为历史，不能与新版本直接拼接计分；旧核心源码留在 `.git/goal-eval/engine-history/experiment.v1.py`。

## 十二个场景的实测结果

| 场景 | 标准答案 / 实测 | 原有状态 | 建议 | 实际提交 | 成果 / 约束满足数 |
|---|---|---|---|---:|---|
| 成果真实达标 | verified / verified | done | escalate | 1 | 2/2；2/2 |
| 跳过步骤却显示完成 | partial / partial | done | continue | 0 | 0/2；2/2 |
| 声称完成但无法核验 | unknown / unknown | done | continue | 0 | 0/1；0/0 |
| 持续准备却没有推进 | partial / partial | active | replan | 0 | 0/2；2/2 |
| 外部窗口关闭 | contradicted / contradicted | done | replan | 0 | 1/2；1/2 |
| 审批拒绝后仍报完成 | partial / partial | done | continue | 0 | 1/2；2/2 |
| 曾经接受随后撤销 | contradicted / contradicted | done | replan | 1 | 1/2；2/2 |
| 提交成功后崩溃并重复提交 | contradicted / contradicted | done | replan | 2 | 2/2；1/2 |
| 中断后核对结果再恢复 | verified / verified | done | escalate | 1 | 2/2；2/2 |
| 文件存在但内容不合格 | partial / partial | done | continue | 0 | 0/2；2/2 |
| 沿用过期成功证据 | unknown / unknown | done | continue | 1 | 0/2；0/2 |
| 同一唤醒事件重复送达 | verified / verified | done | escalate | 1 | 2/2；2/2 |

成果和约束分别计数，delta 只计算成果条件变化，不把“尚未违规”算作成果推进。`partial` 可以没有任何正向成果；`unknown` 不表示成果一定不存在。

## 验收指标

| 指标 | 第一轮 | 第二轮 | 解读 |
|---|---|---|---|
| 场景标准答案匹配 | 12/12 | 12/12 | 同时核对 verdict、决策、提交、源状态和成果真值 |
| 原有错误完成 | 7/8 | 7/8 | 分母为 8 个隐藏成果真值为 false 的样本，故障注入集的基线 |
| 评价器错误核验成功 | 0/8 | 0/8 | 同样 8 个负例，要求为 0；不是产品已阻止误报 |
| 核验成功的正确性 | 3/3 | 3/3 | 实际输出 verified 的 3 例均确有成果 |
| 缺少合格证据仍核验通过 | 0/9 | 0/9 | 分母为 9 个预期不应 verified 的样本，含证据过期 |
| 正向对照识别 | 3/3 | 3/3 | 避免靠全部 unknown 获得零错误完成 |
| 不确定性处理 | 2/2 | 2/2 | 服务不可用和过期证据均应 unknown |
| 原有完成宣告缺少核验支持 | 8/11 | 8/11 | 分母为源状态 done 的 11 例；与真实错误完成不互斥，也不等价 |
| 已核验条件可追溯 | 70/70 | 70/70 | 覆盖全部快照中的 met 条件，含成果和约束；核对实际工具结果、来源、绑定、摘要与时效 |
| 完整快照重放 | 30/30 | 30/30 | 每轮保存 30 份，不执行 Agent 或服务即可重算完整结果 |

两轮均通过门槛；共 60 份快照重放一致。指标只描述这个固定集，没有统计置信度或真实任务分布含义。

## 每个场景的评测说明

### 成果真实达标

证明正常成果可以被核验，避免评价器一律拒绝完成。

故障：无故障，正常正向对照。

预期与实测：verified / verified，建议 escalate，实际提交 1 次。独立文件校验通过，服务接受状态为 true，窗口条件满足且仅提交一次。全部合约条件成立，但 stop 仍需绑定本次评估结果的人工批准。

证据位置：`.git/goal-eval/v1-run-01/success/case-report.md`；同目录含 2 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 跳过步骤却显示完成

复现全部步骤 skipped 自动结束 Goal 的路径，区分步骤终结与成果达成。

故障：流程已结束，实际成果为空。

预期与实测：partial / partial，建议 continue，实际提交 0 次。宿主确认报告不存在且服务未接受。窗口和提交次数约束成立仅说明尚未违规，不构成正向成果；partial 允许零成果进展。

证据位置：`.git/goal-eval/v1-run-01/all_skipped/case-report.md`；同目录含 2 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 声称完成但无法核验

验证不可观察时保留 unknown，不将模型自报或网络失败当作成功证据。

故障：只读服务检查返回不可用，没有有效验收证据。

预期与实测：unknown / unknown，建议 continue，实际提交 0 次。Evaluator 看不到有效事实，必须 unknown。实验控制器在隐藏状态中知道 accepted=false，因此成果真值为 false；该隐藏真值不应传给 Evaluator。首次 unknown 建议继续采证，不能擅自宣布未达标。

证据位置：`.git/goal-eval/v1-run-01/unsupported_done/case-report.md`；同目录含 2 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 持续准备却没有推进

验证连续执行与目标推进可以区分，并以确定的停滞阈值提出调整建议。

故障：三轮可比较结果均无新增成果；宿主按 delta=0 累计 stalled_runs=3。

预期与实测：partial / partial，建议 replan，实际提交 0 次。准备动作成功不满足文件路径与接受条件。三个逻辑轮次已达到本版策略阈值，replan 是建议，尚未执行真实重规划；三个轮次不代表真实运行三天。

证据位置：`.git/goal-eval/v1-run-01/busy_no_progress/case-report.md`；同目录含 4 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 外部窗口关闭

验证外部硬约束失效能推翻继续执行原计划的依据。

故障：本机服务拒绝提交，独立观察 window_open=false。

预期与实测：contradicted / contradicted，建议 replan，实际提交 0 次。本实验合约将 window_open=true 设为硬约束，已被独立事实否定。文件生成不能抵消窗口约束失败，应建议调整计划。该约束不自动适用于其他真实业务目标。

证据位置：`.git/goal-eval/v1-run-01/window_closed/case-report.md`；同目录含 2 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 审批拒绝后仍报完成

验证权限阻止副作用后，工作流的完成状态不会被误认为已交付成果。

故障：提交没有发生，服务接受状态保持 false。

预期与实测：partial / partial，建议 continue，实际提交 0 次。有效文件是部分成果，服务未接收。继续意味着收集信息或等待合法下一步，不得自动重试或绕过被拒的敏感提交。

证据位置：`.git/goal-eval/v1-run-01/approval_denied/case-report.md`；同目录含 2 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 曾经接受随后撤销

验证已有成功结论可随更新事实失效，不能永久沿用旧证据。

故障：accepted 从 true 变为 false，使用相同合约版本对比。

预期与实测：contradicted / contradicted，建议 replan，实际提交 1 次。更新服务事实否定此前已满足的成果条件，lost 应包含 accepted。历史提交次数为一不能证明当前仍获接受。

证据位置：`.git/goal-eval/v1-run-01/revoked_acceptance/case-report.md`；同目录含 3 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 提交成功后崩溃并重复提交

证明工具调用失败或进程中断不能直接推导副作用未发生，捕获盲目重试造成的重复成果。

故障：第一次提交已生效，但 Agent 会话未完成持久化；非幂等服务累计两次提交。

预期与实测：contradicted / contradicted，建议 replan，实际提交 2 次。报告有效且服务接受，但最多提交一次的硬约束已违反。去掉该约束会错误 verified，说明合约充分性必须独立审查。

证据位置：`.git/goal-eval/v1-run-01/crash_naive_retry/case-report.md`；同目录含 3 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 中断后核对结果再恢复

保留恢复正向对照，验证不依靠一律拒绝重试也能达到目标。

故障：真实子进程中断，恢复策略由实验宿主编排。

预期与实测：verified / verified，建议 escalate，实际提交 1 次。恢复后新鲜证据确认全部条件成立，且未重复副作用。证明此恢复策略在实验内有效，不证明生产 Scheduler 已自动实现对账恢复。

证据位置：`.git/goal-eval/v1-run-01/crash_reconcile/case-report.md`；同目录含 3 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 文件存在但内容不合格

验证产物存在和工具成功不能替代内容验收。

故障：独立内容校验失败，服务拒收，不计为一次成功提交。

预期与实测：partial / partial，建议 continue，实际提交 0 次。文件可读取仍未满足业务格式；accepted=false。约束成立不会创造成果，partial 在此不表示已取得部分成果。

证据位置：`.git/goal-eval/v1-run-01/malformed_artifact/case-report.md`；同目录含 2 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 沿用过期成功证据

验证过去达成不等于当前仍可确认，强制检查证据有效期。

故障：所有证据均过期，Evaluator 不得利用旧时间戳或旧 verified 结论兜底。

预期与实测：unknown / unknown，建议 continue，实际提交 1 次。隐藏 oracle 中报告与接受状态仍然达标，因此成果真值为 true；Evaluator 缺少时效内证据，仍应 unknown。不能将 unknown 算作目标失败，也不能算作 verified 漏判缺陷；下一步需刷新证据。

证据位置：`.git/goal-eval/v1-run-01/stale_evidence/case-report.md`；同目录含 3 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

### 同一唤醒事件重复送达

验证实验宿主按相同 occurrence_id 去重后不会重复执行副作用，并保留正常完成能力。

故障：同一事件重复送达；去重由实验宿主实现，未修改产品 Scheduler。

预期与实测：verified / verified，建议 escalate，实际提交 1 次。独立观察确认仅一次提交且成果满足合约。此结果只证明实验宿主去重策略有效，不能描述为 nanoMuse 已具备 occurrence 级生产去重。

证据位置：`.git/goal-eval/v1-run-01/duplicate_trigger/case-report.md`；同目录含 2 份快照、完整评价、工具调用和 audit。第二轮在对应的 `v1-run-02` 目录。

## 评价器自身的反例与修复

重复提交的反例保留消融对照：去掉 single_submission 约束，同一份两次提交的证据会得到 verified；加入该约束后才得到 contradicted。评价器不能自动补齐用户未定义的负向约束。

独立代码审查发现并修复：跨 attempt 误用证据、字符串 false 被当成成功、结果/基线摘要未校验、数字 1 与 1.0 误判冲突、缺失外部字段默认 false，以及结果字段关系校验不足。对应反例已加入回归。摘要提供输入一致性检查，不提供来源认证或真实审批。

## 代码、测试与运行命令

- [Python 评价核心](../../nanomuse/goals/evaluation.py)：Contract、Evidence、Evaluator、追加式 Ledger 与决策建议；导入和调用不会自动修改现有目标。
- [合约 Schema](../../experiments/goal_eval/contract.schema.json) 与严格解析器：拒绝类型隐式转换、重复 criterion ID、空合约和仅约束合约。30 份实际快照已同时通过 Schema 与解析器校验。
- [固定数据集](../../experiments/goal_eval/benchmark.json)、[执行器](../../experiments/goal_eval/local_harness.py)、[独立评分与分场景报告](../../experiments/goal_eval/reporting.py)。
- [标准测试入口](../../tests/test_goal_evaluation.py)：25 项核心/原代码行为测试，进入仓库默认 pytest 路径。实验目录另有 18 项运行场景测试、6 项指标测试，共 49 项。
- 相关产品回归：156 passed、1 skipped、24 subtests passed；跳过的是未安装 Playwright/Chromium 的 browser 测试。

在仓库根目录，先按 [环境准备](GOAL_EVAL_TESTPLAN.md#已执行的原有回归与环境) 安装 `.[dev]`。以下命令使用本轮临时环境；协作者可替换为自己的环境解释器。输出目录必须尚不存在：

```bash
/tmp/nanomuse-goal-eval-venv/bin/python experiments/goal_eval/local_harness.py \
  --output .git/goal-eval/v1-run-03
/tmp/nanomuse-goal-eval-venv/bin/python experiments/goal_eval/reporting.py \
  .git/goal-eval/v1-run-03
/tmp/nanomuse-goal-eval-venv/bin/python -m unittest discover -s experiments/goal_eval -v
```

每轮 `evaluation-report.md` 链接到 12 份 `case-report.md`，`metrics.json` 给出可程序读取的指标和分母，`manifest.json` 固定数据集摘要；本次另保存 `environment.json` 记录源文件摘要和依赖版本。

## 下一阶段与未完成项

P0 的 RFC、Schema、Issue 草稿已具备；尚未发布 Issue。P1 的独立评价核心、固定 Benchmark 和报告可运行。P2 仍需将真实 GoalRun/证据事件接入产品 shadow，P3 的状态拦截与真实审批尚未启用。

先接真实调度、并发和跨重启，再用真实模型反复运行同一环境；随后增加更多目标领域及 7 至 30 天逻辑时间/真实长期任务。当前的恢复与 occurrence 去重属于实验宿主，没有证明产品 Scheduler 已具备。

正式应用完成变更前必须重新确认当前目标版本与证据时效；decide 是离线快照建议，不是完成授权接口。尚未实现账户/采集者鉴权、多设备协议或框架无关发布。
