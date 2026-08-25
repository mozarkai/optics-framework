# Phase 0 execution record

Date: 2026-08-19
Branch: `feat/parallel-sessions-stateless-serve`
Commit range: `2a233a0..10129f0` (20 commits)
Plan: [`2026-08-18-phase0-session-leaks-and-races.md`](../plans/2026-08-18-phase0-session-leaks-and-races.md)
Spec: [`2026-08-18-parallel-sessions-stateless-serve-design.md`](../specs/2026-08-18-parallel-sessions-stateless-serve-design.md)
Audit: [`2026-08-18-parallel-sessions-audit.md`](../specs/2026-08-18-parallel-sessions-audit.md)

A point-in-time record of how Phase 0 was executed: the commits, the decisions taken during execution, the defects found in the plan itself, and what the reviews caught. Written so the next phase does not repeat the mistakes, and so the deferred items have a traceable origin.

For the *current* state of the code, read [Concurrency & Sessions](../../concurrency/index.md). For the backlog, read [Known Issues](../../concurrency/known-issues.md). Where those disagree with this record, they win — this document is history, not documentation.

## Outcome

All 8 planned tasks landed. Every task review passed. The whole-branch review found **one Critical and five Important** findings that no per-task review could have seen; a single fix wave closed all but one, which was deliberately deferred with documentation.

Final suite at `10129f0`: `8 failed, 634 passed, 1 skipped, 2 xfailed, 3 errors`. All 8 failures are in `tests/units/test_optics.py`; all 3 collection errors are in files this branch never touched. Zero overlap with the diff, verified by filename and by `git stash` at four separate points.

## Commits

| Commit | Subject |
|---|---|
| `67b684e` | `docs(specs)`: parallel-session audit and stateless-serve design |
| `572b1e4` | `docs(plans)`: phase 0 implementation plan |
| `e49a1a2` | `fix(serve)`: always evict a session on delete, even if teardown fails |
| `7cad2b4` | `docs(concurrency)`: concurrency and sessions documentation section |
| `e91f2d4` | `docs(plans)`: fix pre-commit invocation form and task 1 test |
| `b61f78e` | `fix(sessions)`: make terminate_session cleanup unconditional |
| `e65d7b2` | `fix(serve)`: reclaim the device when session auto-launch fails |
| `2204a74` | `fix(serve)`: preserve launch error when cleanup also fails |
| `718dc45` | `fix(serve)`: scope request template overrides to the request |
| `8dcf1b7` | `test(serve)`: bound the template-override isolation test |
| `f95262a` | `fix(serve)`: serialize workspace stream capture against keyword execution |
| `b69324b` | `fix(serve)`: guard api-data swap and end event stream with its session |
| `dd0e850` | `fix(events)`: own EventManager lifecycle per session, not per request |
| `d7101bb` | `fix(serve)`: reject `--workers>1` instead of silently losing sessions |
| `62fbee3` | `fix(serve)`: stop logging raw capabilities, disallow credentialed wildcard CORS |
| `dd54c1e` | `fix(serve)`: serialize session teardown and 404 on unknown session |
| `5ea1b9e` | `fix(events)`: cancel the dispatch task on the loop that owns it |
| `1682f67` | `fix(drivers)`: redact credential-bearing capability values in logs |
| `4773a80` | `fix(serve)`: harden request-scoped cleanup and event-manager bookkeeping |
| `10129f0` | `docs(concurrency)`: sync the concurrency pages with landed phase 0 |

## What the whole-branch review caught

The per-task reviews were thorough and each passed. They still could not see these, because every one of them lives *between* two diffs.

### C1 (Critical) — a lock acquisition was removed while another was being added

Task 1 deleted the `close_and_terminate_app` pre-call from `delete_session` as redundant. It *was* redundant for teardown — `terminate_session` performs the driver quit itself. But that call routed through `KeywordExecutor.execute`, which meant it was also **the only thing acquiring `session.keyword_lock` before driver teardown**.

So the phase simultaneously added lock coverage to the workspace stream (Task 4) and silently removed it from the teardown path (Task 1). The result: a `DELETE` could `driver.quit()` while another request's WebDriver command was in flight, and `rmtree` the template directory while a matcher was reading from it.

**Why the pre-flight scan missed it.** The scan checked whether any task *added* a nested `keyword_lock` acquisition — correct, and it found none. It never considered that a task might *remove* an existing one. A removed acquisition is invisible in the diff that removes it (the call simply disappears) and invisible in the diff that depends on it (nothing changed there).

**Lesson for future phases:** when auditing lock coverage across a multi-task change, enumerate the call sites *before* and *after* and diff the sets. Do not only look for additions.

### I1 (Important) — two independently-correct fixes interacted

Task 1 moved `terminate_session` onto `asyncio.to_thread`. Task 6 changed `EventManagerRegistry.remove_session` from `stop()` to `shutdown()`. Individually fine. Together, `Task.cancel()` now ran **in a worker thread against a task owned by the main event loop**.

This is silent: `Task.cancel()` schedules through `loop.call_soon()`, whose `_check_thread()` guard only raises under `loop.set_debug(True)`. Off-loop it appends to the ready queue without waking the selector and races the loop thread's own `_wakeup_next()`.

Fixed by capturing the owning loop in `EventManager.start()` and cancelling via `call_soon_threadsafe` when reached off-loop.

### I3 (Important) — the claim outran the code

Task 8 was titled "stop logging raw capabilities". It fixed `create_session`'s `config.model_dump()` line. But `create_session` forces `log_level=DEBUG` for every serve session, and the Appium driver logs `final_caps` and `all_caps` at DEBUG on every launch, and `SensitiveDataFormatter` only masks `@:`-style URL credentials. So `browserstack.key` still reached stdout — one call after the line that was fixed.

**Why the task review missed it:** the test patched `internal_logger.info` only. The leak was at DEBUG. A test that patches one level cannot prove a claim about all logging.

**Lesson:** when the requirement is "X never appears in logs", attach a real handler at the lowest level and assert on emitted output — do not patch a method.

### I4 (Important) — the branch's own docs contradicted its own code

`docs/concurrency/parallel-session-limits.md` was written during this phase, marked *"Status: Current — the page to trust before deploying anything"*, and then the same branch fixed five of the things it described as broken. `REST_API_usage.md` still documented a keyword call that had been deleted.

**Root cause:** the docs were written describing the pre-branch state, in the middle of a branch that changed that state, and never revisited.

**Lesson:** a "Status: Current" marker is a claim that has to be re-verified at the end of any branch that touches the described behaviour. Docs written mid-change need a re-read gate before merge.

### I5 (Important) — a silent API contract change

With the `close_and_terminate_app` pre-call removed, `terminate_session` no-ops on an unknown id, so `delete_session` fell through to `200 TERMINATED`. Previously a 404 from the pre-call surfaced (as a 500 — also wrong, but not a false success). Now fixed to a proper 404.

### I2 (Important) — deliberately not fixed

`asyncio.to_thread` is not cancellable, so cancellation releases `keyword_lock` while the driver command is still running in the abandoned thread. This cannot be fixed by adding a lock; it needs Phase 1's per-session single-worker executor, where serialization becomes structural.

**Decision:** document rather than partially fix. A partial fix would be load-bearing on an executor design that does not exist yet. The roadmap, the limits page, the async model page, and the section index all now name it explicitly.

## Decisions taken during execution

Each was made without blocking, and each is recorded with what it would cost if wrong.

| # | Decision | Cost if wrong |
|---|---|---|
| R1 | Work on the branch in the primary tree rather than a separate git worktree | None material — the branch already isolates from `main` |
| R2 | Briefs are anchored at an old commit; implementers locate every edit site by symbol name, never line number | A wrong-region edit; caught by the task review's diff |
| R3 | **Plan defect** — Task 7's test dir is `tests/units/helpers/` (plural), not `helper/` | A stray directory |
| R4 | **Plan defect** — Task 7's entry point is `run_uvicorn_server`, not `run_server` | Tests fail at the RED step, immediately visible |
| R5 | Task 4's lock goes *inside* the existing `try:`; verified no nested acquisition exists | A hung workspace-stream test |
| R6 | `gitleaks`/`commitizen` hooks cannot install locally → run the other four individually, check commit messages by hand | A secret or malformed message slips through |
| R7 | **Plan defect** — Task 1's test injected the failure into `execute_keyword`, which the fix removes; re-pointed at `terminate_session` | The test would assert on a seam the code no longer uses |
| R8 | `httpx` undeclared → tracked as a follow-up rather than folded into an unrelated task | CI on a clean checkout fails loudly at collection, not silently |
| R9 | **Expanded Task 1's scope** into `session_manager.py` — finding G1 was not closed without hardening `terminate_session` itself | A larger diff for one task; the alternative left the branch claiming a fix it had not made |
| R10 | Fixed a plan-mandated defect rather than shipping it (cleanup error masked the launch error) | One extra review cycle on a rare failure path |
| R11 | Fixed a test that would *hang* rather than fail on regression | One extra cycle, test-only change |
| R12 | Batched Tasks 4+5 into one dispatch (small, disjoint, same shape) | A finding in one drags the other into the same fix round |
| R13 | **Plan defect** — Task 5's test referenced a nonexistent `ApiRequest` model; adapted to the endpoint's real dict body | The test exercises a different entry shape than production |
| R14 | One fix wave for the final review; I2 documented rather than fixed | Ships a known, documented hole |

**Four of those (R3, R4, R7, R13) were defects in the plan itself**, caught by implementers or reviewers rather than by the plan's own self-review. The plan's self-review checked for placeholders, internal contradictions, and type consistency — it did not verify that named symbols and paths actually exist in the repo.

**Lesson:** a plan's self-review should include a mechanical existence check — grep every file path, function name, and class name the plan asserts, and confirm each resolves.

## Process notes

- **Batching worked.** Tasks 4+5 and 7+8 were each dispatched as one unit and reviewed as one diff, halving the cycle count for four small same-shape tasks with no loss of scrutiny. Tasks 4+5 was the first review in the phase to pass with zero findings.
- **Implementers caught plan defects reliably.** Three of the four plan defects were found by the implementer transcribing the brief, not by review. Instructing them to report rather than guess was load-bearing.
- **The final review earned its cost.** Every per-task review passed. The whole-branch review found a Critical. The two interaction findings (C1, I1) are structurally invisible to a per-task view.
- **`pre-commit run` accepts one hook id per invocation.** Passing several fails with `unrecognized arguments` and then runs *nothing* — a silent verification skip. The plan initially told every task to pass four at once; corrected in `e91f2d4`.

## What Phase 1 must carry forward

1. **The `contextvars` trap.** `asyncio.to_thread` copies context; `loop.run_in_executor` does not. Phase 0's template-override fix depends on that copy. The executor swap must dispatch through `contextvars.copy_context().run(...)`, and needs a test asserting an override still resolves.
2. **Release the lease on work-item completion, not coroutine cancellation.** Otherwise I2 survives the executor that was supposed to close it.
3. **Teardown must be a lease operation.** `delete_session` needs to participate in whatever serialization Phase 1 introduces, or C1 reappears — and in the distributed form of Phase 6 it would be far harder to see.
4. **`EventManager` needs explicit loop affinity.** As more session-lifecycle work moves off the loop, every `asyncio` primitive on `Session` needs a known owning loop, not lazy binding.
5. **`session.optics.build(...)` is only safe because it never awaits.** Making engine instantiation async would let two concurrent first-keywords double-instantiate a driver.
