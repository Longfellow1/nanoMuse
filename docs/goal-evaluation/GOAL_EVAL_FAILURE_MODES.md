# 从本机失败复现建立 Goal Evaluation

本页记录最初 9 场景发现。最新 v1 已扩展为 [12 场景评测集](GOAL_EVAL_DATASET.md)，并完成 [两轮实测与逐场景报告](GOAL_EVAL_V1_REPORT.md)；下文数字为初始跑次，不与新版本混合计分。

## 产品判断与研究顺序

要解决的是“用户能否放心把一个目标长期交给 Agent”。第一步先建立可独立观察的目标结果，再比较 Agent 记录的进展和实际结果；把差异归为稳定的失败模式，然后让每个模式都有一个可重复运行的场景。完成这些后再扩展真实模型、长期运行和更多平台。

原来的 17 项测试验证了评价规则和本地持久化，没有跑真实 Agent 工具链。本轮补上本机场景执行器，用原有 `MuseAgent -> Sentinel -> tools -> GoalStore` 跑脚本化行为，独立检查文件和本机 HTTP 服务，再输出评价。没有改产品源码，也没有把脚本化行为包装成模型自然产生的失败。

```text
固定目标与验收约束
  --> 场景执行器 + 故障注入
  --> nanoMuse Agent / 工具 / Sentinel
  --> 文件与本机服务产生实际状态
  --> 独立检查器读取结果
  --> 原有目标状态 vs 证据评价 vs 场景标准答案
  --> 失败归类、决策建议、快照重放
```

## 本机已跑通什么

源码基线：`ae9eb06b37780dd5028ab885cc38625bc7d830c0`。运行环境：Python 3.12.9；模型为仓库原有 MockLLM；HTTP 只绑定 loopback，不访问生产服务。**真实的是 Agent 执行、权限判定、文件读写、服务提交和子进程退出；模型选择行为是预设脚本。**

目标示例：“生成格式有效的周报，服务确认接受，提交窗口有效，且最多提交一次”。执行者只能改自己的 workspace，验收服务的数据库放在 workspace 外；服务控制器负责注入关闭窗口、撤销结果、暂时不可用等故障。独立检查器不相信 Agent 的总结，也不从 Goals/Feed 的旧进度推导事实。

实测：**9 个场景的 verdict 均匹配预先定义的场景标准答案；其中 6 个场景原有目标状态为 done，但独立证据不支持成功。** 这是有意注入失败后的对照结果，不能解读为产品 6/9 的实际错误率。

| 场景 | 原有目标状态 | 独立评价 | 实际提交次数 | 产品含义 |
|---|---|---|---|---|
| 正常生成并提交 | done | verified | 1 | 正常成果可以得到确认 |
| 两步全部跳过 | done | partial | 0 | 步骤处理完不代表成果存在 |
| 直接报完成、验收服务不可用 | done | unknown | 0 | 无法确认必须显式暴露 |
| 连续三轮只重做准备材料 | active | partial，delta 连续为 0 | 0 | 持续活动需要与持续推进区分 |
| 提交窗口已关闭 | done | contradicted | 0 | 外部前提失效必须反馈给规划 |
| 提交被 Sentinel 拒绝后仍报完成 | done | partial | 0 | 权限控制不能代替完成验证 |
| 服务已撤销先前的接受状态 | done | contradicted | 1 | 旧的正确结论也可能失效 |
| 提交后进程退出，盲目重试 | done | contradicted | 2 | 目标达成的同时可能违反约束 |
| 提交后进程退出，先查结果再恢复 | done | verified | 1 | 恢复前核对结果可以避免重复行动 |

中断实验在工具提交已完成、Agent 还未持久化 session 时，通过子进程 `os._exit(73)` 突然退出。父进程检查 exit code 和服务实际计数，重新打开 GoalStore/ledger。盲目重试与先 reconcile 是实验控制器提供的两种恢复策略，**不代表当前 nanoMuse scheduler 已实现或必然采用其中一种**。

## 失败模式与解决位置

| ID | 失败模式 | 本轮证据性质 | 系统需要补什么 |
|---|---|---|---|
| F01 | 任务账本被当成目标结果 | 真实 Goal 工具链复现 all skipped -> done | 目标完成单独核验，不以步骤计数直接结案 |
| F02 | 无法验证却报完成 | 真实 set_status 写入 + 本机服务 503 | unknown、缺失证据原因、有限验证预算 |
| F03 | 重复忙碌没有目标推进 | 三轮真实文件写入和步骤更新，调用均成功 | 跨逻辑 run 比较结果，连续停滞触发重新规划 |
| F04 | 原计划依赖的条件失效 | 本机服务窗口关闭，提交返回失败 | 独立维护 invariant，暂停依赖失效前提的行动 |
| F05 | 行动被拒绝但状态照样更新 | Sentinel 实际 deny，提交数 0，Goal 仍 done | 结果与允许/拒绝记录关联，不能靠后续总结覆盖事实 |
| F06 | 旧证据被当成永久证明 | 服务先 accepted，再由控制器撤销 | 重查、有效期、回退记录；允许进度下降 |
| F07 | 动作已生效但本地没记完，重试造成重复 | 子进程突然退出后服务计数 1；盲目重试后计数 2 | 先 reconcile，再决定 retry；副作用单独计数 |
| F08 | 评价合约只写正向成果，漏掉负向约束 | 同一重复提交快照，移除唯一提交约束后评价变 verified | 合约要包含“做成什么”和“不能造成什么”；检查器正确也不代表合约充分 |

F08 是对 Evaluator 自己的反例。仅检查“报告有效、服务接受、窗口开放”，重复提交也会被评为 verified；加上事先定义的 `single_submission` invariant 后才返回 contradicted。这个结果说明评价体系必须测试自己的盲点，不能把一组通过的单元测试当作可靠性的证明。

## 已搭好的最小 evaluation 体系

| 部件 | 当前能力 | 边界 |
|---|---|---|
| 场景集 | 9 个命名场景，预定义标准 verdict 和独立业务计数断言 | 场景有限，尚无真实用户任务分布 |
| 执行器 | 原有 MuseAgent/MockLLM/Files/Goals/Sentinel | 未覆盖 MuseService scheduler、Android/iOS/desktop |
| 受控环境 | 真实本机 HTTP 服务、SQLite 状态、workspace 产物 | 专用测试服务，不是生产 connector |
| 故障注入 | 503、关闭窗口、撤销接受状态、审批拒绝、突然退出 | 不是手机 Doze/reboot 或随机网络故障 |
| 独立评价 | 读取实际文件/服务，四类 verdict、差量、invariant | host 信任边界，未实现外部 producer 鉴权 |
| 策略对照 | 继续、三轮停滞重新规划、完成复核、恢复前 reconcile | recommendation；产品仍沿用原行为，无 enforcement |
| 可追溯产物 | contract/run/evidence/result、工具 call ID、audit、session、服务状态 | 受控数据；不保存真实用户信息 |
| 重放 | 保存的 23 个评价快照都可不执行 Agent/服务而重算 | 输入中的 UUID 会变化，跨整套运行比较语义结果，不比较 UUID |

代码：[local_harness.py](../../experiments/goal_eval/local_harness.py)、[test_local_harness.py](../../experiments/goal_eval/test_local_harness.py)。现有评价核心：[prototype.py](../../experiments/goal_eval/prototype.py)。

## 本机复现与查看结果

在仓库根目录、已安装 `.[dev]` 的环境中运行。本轮环境安装说明见 [测试计划](GOAL_EVAL_TESTPLAN.md)。输出目录必须不存在，避免覆盖上一次证据：

```bash
/tmp/nanomuse-goal-eval-venv/bin/python experiments/goal_eval/local_harness.py \
  --output .git/goal-eval/v1-run-03
/tmp/nanomuse-goal-eval-venv/bin/python experiments/goal_eval/reporting.py \
  .git/goal-eval/v1-run-03
```

初始产物保存在 `.git/goal-eval/local-20261017-01/`；最新 v1 实跑在 `.git/goal-eval/v1-run-01/` 和 `v1-run-02/`。打开最新目录的 `evaluation-report.md` 看指标与分场景链接，`metrics.json` 看结构化结果；每个场景目录保留独立快照、评价、`calls.jsonl`、`audit.jsonl` 和数据库。`manifest.json` 保存源码版本、环境与预期结果。不将这些生成数据加入 Git。

不重启服务的重放命令：

```bash
/tmp/nanomuse-goal-eval-venv/bin/python experiments/goal_eval/local_harness.py \
  --replay .git/goal-eval/v1-run-01/crash_naive_retry/work.2.snapshot.json
```

自动回归：

```bash
/tmp/nanomuse-goal-eval-venv/bin/python -m unittest discover -s experiments/goal_eval -v
```

本轮 **28 tests passed**：原先 17 项 + 新增 11 项 runtime harness 测试。9 个本机场景另有一次持久输出运行，结果同样为 9/9 verdict 匹配。Ruff 与两个执行模块的 mypy 检查通过。

上段是初始验证数量；当前 v1 为 49 项。旧实验的评价版本为 experiment.v1，当前版本为 goal-eval.v1，不能用新版本直接拼接或重放旧评价；旧源码留在 `.git/goal-eval/engine-history/experiment.v1.py` 供历史复核。

## 接下来按什么顺序扩展

1. **固定场景与评价语义。** 加入“规则自身会错”的反例，保留正常成功对照；将标准答案、风险约束与执行策略分开维护，避免为每个 badcase 临时改一条规则。
2. **接真实后台链路。** 增加调度触发、并发检查、跨重启 run/attempt 关联及连续停滞计数。先在 Python 本机测，再在 Android 真机测休眠/重启；通过前不宣称 L3 可靠。
3. **用真实模型跑同一环境。** 替换脚本 actor，保持世界状态和 checker 不变，按固定预算、多次重复测误报完成、有效进展、重复副作用和恢复效果。届时才开始报告模型实际失败率。
4. **再接真实目标与业务来源。** 先选择文件/业务状态可核查的目标，逐步扩展用户自述、复杂文本与长期习惯；无法可靠核验的部分保留 unknown。
5. **最后抽框架公共层。** 当多个 Agent 使用同一任务环境、证据格式和标准答案可以比较时，再讨论框架无关 harness。当前优先解决 nanoMuse 的可复现闭环。

最小产品验收不是“测试数字增加了”，而是用户能看到：哪条目标条件变化了、依据是什么、为什么下一步这样做；同一失败复现后，方案修改确实改善了结果，并保留对正常任务的支持。
