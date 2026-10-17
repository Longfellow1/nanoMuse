# Feature issue draft

Local draft only. Do not publish automatically. Matches `.github/ISSUE_TEMPLATE/feature_request.yml` and the contribution guidance to discuss larger changes before implementation.

**Suggested title:** Goals: evaluate progress against observable evidence

**Suggested labels:** enhancement, runtime

## What you are trying to do

I want a recurring goal check to distinguish work performed from progress toward the person's objective. For example, generating a report is useful preparation, but a goal that also requires acceptance by a service is incomplete until that service confirms acceptance.

A source audit at `ae9eb06b37780dd5028ab885cc38625bc7d830c0` found that the Python runtime counts done and skipped steps as progress and automatically completes a goal when all steps have those statuses (`nanomuse/goals/store.py:140-143,480-505`). `set_status` also permits completion without checking the steps or any evidence (`:358-365`). On Android, `GoalFlow.afterTurn` parses the model's `nanomuse-goal-update` block and `GoalStore.applyUpdate` stores its progress and status directly (`android/src/android/app/src/main/java/io/github/nanomuse/goals/GoalFlow.kt:63-90`, `GoalStore.kt:45-60`). These are different implementations, so a Python change would not cover every client.

This is based on local source characterization and offline fixtures, not a claim that a real user's goal was falsely completed. In particular, the actual Python store can complete a goal after all its steps are skipped, and can mark a goal done while its only step is still pending.

## What you would like to happen

Add an optional goal contract with explicit, measurable acceptance conditions and a separate evaluation record. Each record would identify the criterion, the evidence source and observation time, the associated run and action, and the reason for accepting or rejecting that evidence.

The initial scope could be the Python runtime, in shadow mode. Existing progress would remain available, while the new result would say `verified`, `partial`, `unknown` or `contradicted`. A completed task or a model's completion claim would not, by itself, verify a condition. An unavailable checker would return `unknown`, rather than inventing progress. `partial` would mean incomplete but at least partly assessable, and could include zero verified conditions.

For the report example, an update might show:

```text
Report format: verified by a local checker.
Service acceptance: unknown, the status lookup timed out.
Overall: partial. Progress since the previous check: unknown.
Next: check acceptance before submitting again.
```

The first implementation would use deterministic predicates over independently collected facts, with replayable snapshots and explicit freshness rules. Sentinel audit records and operator traces could supply references and action context, but a successful tool result or a short audit preview would not be treated as acceptance evidence. A model could help draft conditions or explain the result; it would not grant verification by judging its own report.

Evaluation would return a recommendation to continue, propose a revised plan, request help, or stop. Revised plans would use the existing proposal/acceptance flow where available. In a later opt-in enforcement stage, completion would require the necessary verified conditions and user confirmation. Sensitive actions would continue through the platform's existing permission controls. The first shadow stage would report discrepancies without claiming to prevent all unsupported completion.

A bounded first PR could add the contract, pure evaluator, separate evaluation store and offline tests. Runtime hooks, user-facing controls and Android integration could follow independently. It would not require another model call, a framework-wide refactor or a general benchmark service.

The local experiment currently covers five core cases: actual progress, successful work without progress, a completion claim without evidence, invalidated external conditions, and interruption followed by a retry. It also checks stale or mismatched evidence, conflicts, artifact changes and duplicate evaluation delivery. These tests do not establish real-device recovery, external producer authentication or exactly-once side effects.

A local first implementation now provides a standalone Python evaluator and a versioned 12-scenario fault-injection benchmark. Two independent local runs matched all scenario expectations, including three valid success controls and two cases requiring an unknown result. In the eight known negative cases, the existing goal store was marked done seven times; the evaluator verified none of those eight. These are controlled fixtures driven by MockLLM, not an estimate of a real model's failure rate, and the evaluator does not yet enforce product goal states.

## What you do today instead

Inspect the goal's conversation, tool results and artifacts manually. The existing progress display is useful as a task ledger, but its evidence must be checked separately. A second model review could provide an explanation, but would still need independent facts to verify progress.

The roadmap already proposes an open evaluation suite for the hands and provenance for memory (`docs/roadmap.md:61-65`). Goal evaluation would be a smaller, complementary check of whether ongoing work satisfies an agreed objective. The audit did not find a dedicated proposal in the issue and PR titles and bodies retrieved on 2026-10-17; comments and Discussions have not been exhaustively reviewed.

## Area

Goals, memory, proactivity

## Maintainer discussion

Would a Python-only shadow implementation be a useful first contribution, with the acceptance contract and offline evaluator reviewed before adding runtime hooks? If the preferred starting point is Android's goal checks, the same fixtures could define the semantics while keeping the implementation in Kotlin.

## Local supporting material

- [Source audit](GOAL_EVAL_AUDIT.md)
- [Design RFC](GOAL_EVAL_RFC.md)
- [Test plan and results](GOAL_EVAL_TESTPLAN.md)
- [Isolated experiment](../../experiments/goal_eval/prototype.py)

These relative links refer to this local proposal. Before manually publishing, provide reviewer-accessible links or attach the relevant excerpts. No issue number or publication URL exists yet. Future commits should use Conventional Commits and a DCO sign-off; a follow-up PR should reference `For #N` after an actual issue has been agreed.
