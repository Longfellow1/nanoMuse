# GoalEval 协作入口与下一阶段验收

## 当前是否达到目标

**已达到：** 第一版可复现的本机验证基线。评价核心、合约 Schema、12 个有预设真值的场景、独立评分、逐场景报告和回放已可运行。两轮固定集结果一致，核心测试进入默认 pytest；完整场景集与证据评分当前仍需手动执行。

**尚未达到：** nanoMuse 已运行 evidence-based Goal 功能，或已经可靠阻止错误完成。评价模块没有接入真实 GoalRun，没有接管原有 done 行为，真实审批和外部来源认证也没有实现。脚本模型结果不能证明真实模型或长期任务的可靠性。

阅读顺序：[v1 实测报告](GOAL_EVAL_V1_REPORT.md)、[数据集与指标](GOAL_EVAL_DATASET.md)、[架构 RFC](GOAL_EVAL_RFC.md)。源码基线审计见 [Audit](GOAL_EVAL_AUDIT.md)。

## 建议分开的协作任务

| 任务 | 可独立交付的范围 | 验收标准 | 依赖 |
|---|---|---|---|
| A. 真实运行的影子观测 | Python GoalRun 的开始/结束/失败上下文、goal/revision/run/attempt/call 关联、评价事件 | 通过真实 service/scheduler 路径得到可追溯评价；普通聊天不串目标；原状态策略不变 | 先固定运行事件接口 |
| B. 证据与合约可信边界 | 用户确认合约、来源/权限绑定、受保护快照、时效与删除策略 | 模型不能自封 collector；跨账号、跨目标、错误版本及过期证据均不能核验通过 | 与 A 共享记录字段 |
| C. 恢复和并发可靠性 | 重复调度、多个 worker、started 租约、动作后崩溃的 reconcile | 真实调度器重复交付和重启不重复提交；记录不丢失；先核对外部状态再决定重试 | A 的真实 run/attempt |
| D. 真实模型评测 | 在同一固定环境、预算和真值下替换 MockLLM，保存模型/提示词/重复次数 | 分开报告 Agent 完成宣告错误、Evaluator 错误、弃答和副作用；保留全部跑次 | 场景集与 checker 保持稳定 |
| E. 可选的决策介入 | 用户 opt-in、完成确认、计划建议、状态更新与 schedule 控制 | 覆盖 set_status、update_step 自动完成、all-skipped、API 手动覆盖；应用时重查版本与证据时效；幂等生效 | A、B、C 验证后再启用 |
| F. 长期与跨端验证 | 7 至 30 个逻辑日、故障/事件注入；再做实际长跑及手机协议 | 提供跨 run 的有效进展、停滞、回退、恢复延迟和重复副作用；真机验证休眠/重启 | Python 闭环先稳定 |
| G. Benchmark 的 CI 门禁 | 将 experiments/goal_eval 纳入变更触发，运行完整场景集与评分并保存证据 | 数据集或规则变更触发检查；不通过时禁止合并；报告可下载复核 | 需有 GitHub workflow 写入权限 |

可先并行做 A、B、D 的接口设计和独立测试；C 基于 A 的事件接口实现，E 最后接管状态。不要让多个 Agent 同时修改 `nanomuse/server/service.py` 或同一段 Sentinel 代码，接口先以最小记录结构约定，再分别提交。

## 协作时必须保留的原则

- 明确区分 Activity、成果条件和约束；不能靠调用数、持续时间或约束满足数证明进展。
- 产物真实存在却证据过期时可以 unknown；unknown 不是失败事实，也不能核验为完成。
- `decide()` 是受信宿主对快照的建议函数。摘要不是身份认证，`approved_result_id` 不是可暴露给模型的审批接口。
- 实验 Host 的 occurrence 去重和恢复策略不能称为产品 Scheduler 已实现。
- 固定 Benchmark 的 false-completion 为 0 只是回归门槛。必须保留正向对照，防止全部 unknown 冒充改进。
- 修改样本、真值或规则要解释原因并保留版本；不要为了通过评测删掉失败样本或改小指标分母。

## 本机与 CI 的最短验证路径

在仓库根目录准备独立环境，以下示例适用于 POSIX shell：

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m unittest discover -s experiments/goal_eval -v
.venv/bin/python experiments/goal_eval/local_harness.py --output /tmp/nanomuse-goal-eval-collab-01
.venv/bin/python experiments/goal_eval/reporting.py /tmp/nanomuse-goal-eval-collab-01
```

输出目录须不存在。`evaluation-report.md` 链接 12 个逐场景报告，`metrics.json` 给出分母，快照与调用记录可回放。现有 `Test Suite` 会执行默认 tests/ 下的核心测试，但尚未接入 experiments/goal_eval 场景集。完整 Benchmark 的 CI 改动已在本机准备，因推送凭据缺少 workflow 权限未纳入本 PR；本机补丁保存在 `.git/goal-eval/ci-integration.patch`，原提交保留在 `feat/goal-eval-v1-with-ci` 分支。

## 向官方上游贡献之前

当前 fork 用于协作，没有向 nano-muse/nanoMuse 发布 Feature Issue 或 PR。上游贡献应先用已准备的 [英文 Issue 草稿](GOAL_EVAL_ISSUE_DRAFT.md) 确认方向，再把评价核心、Benchmark 和 shadow 接入拆成可审查的小 PR；按上游规范补齐面向用户的英文/中文文档、DCO 和相应验证。
