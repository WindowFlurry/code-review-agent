# Design

How the code-review agent is put together, and why each decision was made the
way it was.

---

## 1. The problem worth solving

A code-review agent has one dominant failure mode: **it invents defects that
sound right**. LLMs are extremely good at producing plausible-sounding problems
with confidently-quoted code, and a reviewer reading the output will check one of
them against the file. If that line does not exist, the entire report becomes
worthless — including the parts that were correct.

So the central question is not "how do we make the model find bugs?" but **"how
do we make it impossible for the model to report something untrue?"**

The answer here has two parts:

1. **Compute what can be computed.** Anything derivable exactly — file inventory,
   line numbers, lint results, structural metrics — is derived exactly, by tools.
   Those results are true by construction and can anchor everything else.
2. **Never let the model supply the evidence.** The model proposes *where* a
   defect is. Code is then re-read from disk, and a claim whose snippet is not
   found there is discarded.

Everything else follows from those two commitments.

---

## 2. Architecture

```
                            START
                              │
                              ▼
        ┌──────────────────────────────────────────┐
        │  ingest                 DETERMINISTIC     │
        │  walk repo · classify · skip              │
        │  binary / oversized / generated           │
        │  empty repo ──────────────────────────┐   │
        └───────────────────────────────────────┼───┘
                              │                 │
                              ▼                 │
        ┌──────────────────────────────────────┐ │
        │  static_scan            DETERMINISTIC│ │
        │  ruff over the repo · AST per file   │ │
        │  → ground truth, zero tokens         │ │
        └──────────────────────────────────────┘ │
                              │                 │
                              ▼                 │
        ┌──────────────────────────────────────┐ │
        │  plan                   LLM           │ │
        │  prioritised task list from a digest │ │
        │  + folds in the deterministic finds  │ │
        └──────────────────────────────────────┘ │
                              │                 │
                    ┌─────────┴─────────┐       │
                    │  MAP: Send × N    │       │
                    ▼        ▼        ▼       │
        ┌──────────────────────────────────────┐ │
        │  review_file ×N         LLM + TOOLS  │ │
        │  ReAct sub-agent per file/window     │ │
        │  → EVIDENCE BINDING (re-read from    │ │
        │    disk, byte-match, else discard)   │ │
        └──────────────────────────────────────┘ │
                              │                 │
                              ▼                 │
        ┌──────────────────────────────────────┐ │
        │  verify                 LLM           │ │
        │  adversarial second agent: default   │ │
        │  reject, unless the code proves it   │ │
        └──────────────────────────────────────┘ │
                              │                 │
                              ▼                 │
        ┌──────────────────────────────────────┐ │
        │  aggregate              DETERMINISTIC│ │
        │  dedupe · sort · cap                 │ │
        └──────────────────────────────────────┘ │
                              │                 │
                              ▼                 │
        ┌──────────────────────────────────────┐ │
        │  report                 DETERMINISTIC│ │
        │  console · Markdown · JSON           │◄┘
        └──────────────────────────────────────┘
                              │
                             END
```

### Design patterns

| Pattern | Where | Why |
|---|---|---|
| **ReAct** (reason → tool → observe) | `review_file` | A reviewer must *look* before it claims. Tools turn "I think this is wrong" into "line 17 passes user input to eval". |
| **Reflection / generator–verifier** | `verify` | The agent that produces a finding is the worst judge of it. A separate agent, with an adversarial prompt, catches what self-review misses. |
| **Map-Reduce fan-out** | `fanout` + `operator.add` | Files are independent, so review them concurrently and merge. Keeps cost linear instead of quadratic. |
| **Plan-and-Execute** | `plan` → `fanout` | Prioritising before spending tokens matters on a repo bigger than the budget. |
| **Tool grounding** | `tools/` | Every finding traces to a tool call. |
| **Deterministic prefetch** | `ingest`, `static_scan` | The cheap, reliable work happens before any token is spent. |
| **Hierarchical agent (agent-as-tool)** | sub-agents | Per-file sub-agents under a supervisor graph keep each context small. |
| **Fail-closed guardrails** | `verification/evidence.py` | Ambiguity resolves to *rejection*, never to *acceptance*. |

---

## 3. The anti-hallucination gate

`src/codereview/verification/evidence.py` is the most important file in the
project. It enforces one rule:

> **The code in the final report is always read from disk, never taken from the model.**

The model returns a `CandidateFinding` with a path, a line range, and the quoted
lines. `bind_evidence()` then:

1. Resolves the path inside the repository root (sandbox check).
2. Re-reads the file with encoding fallback.
3. Validates the line range against the real file length.
4. Normalises whitespace and strips `NNNN: ` line-number prefixes from the
   model's snippet, then checks it appears in the cited lines.
5. If it does not, retries against a ±5-line window — models routinely cite the
   right code with a line number off by one, and rejecting those would throw away
   true positives over a cosmetic error.
6. On success, **overwrites the snippet with the text read from disk** and records
   its SHA-256.

The model has no influence over the text that reaches the report. This also makes
the gate immune to prompt injection from the code under review: a source file
cannot talk its way into the output, because the model is not the one writing it.

**Rejections are kept, not dropped.** `VerifiedFinding` represents both outcomes,
so the report footer can print:

```
candidates  10
  rejected  2  (2 by evidence check, 0 by verifier)
```

That line is the claim *and* the evidence for it. On `tests/fixtures/clean_repo`,
where any finding is by definition a false positive, the same machinery should
yield `confirmed=0`.

### Why the gate is deterministic and the verifier is not

The two guards are deliberately different in kind:

- **Evidence binding** answers *"is this code real?"* — a decidable question, so
  it is decided by string comparison. Free, instant, unfoolable.
- **Verification** answers *"does this real code have this problem?"* — a
  judgement call, so it is made by a model. Fallible, but a *different* model
  invocation with an opposing prior.

Doing the cheap deterministic pass first means the expensive LLM verifier only
runs on findings that are already known to cite real code — fewer calls, and each
one focused on the actual question.

---

## 4. Deterministic vs LLM: where the line is

The line is drawn at **"can this be computed exactly?"**

| Computed | Model's job |
|---|---|
| Which files exist, how big, what encoding | Which files deserve attention |
| Every ruff diagnostic and its line numbers | Whether a readable defect is *actually* a defect |
| Function boundaries, complexity, nesting depth | Whether the complexity is *justified* |
| Bare `except:` sites, mutable default arguments | Whether a swallowed error matters *here* |
| Whether a quoted snippet exists at a cited line | Whether the described behaviour occurs |

The practical consequence: **the report can be wrong about judgement, but never
about facts.** Every citation, every line number, and every code snippet in the
output is verifiable by opening the file.

Tool-sourced findings (ruff, AST) skip both the evidence-matching step and the
LLM verifier. There is no claim to check — an analyser reported it, and its line
numbers *are* ground truth. Letting a model reject a ruff diagnostic would be a
downgrade, not a safeguard.

---

## 5. Tool design

All tools are read-only, path-sandboxed, size-capped, and time-limited. The
agent cannot execute, modify, or delete the code it reviews.

| Tool | Purpose | Notes |
|---|---|---|
| `list_repo_files` | Enumerate files | The model cites paths from here, so it cannot invent one |
| `read_source` | Read with line numbers | Binary sniff, encoding fallback chain, byte cap |
| `search_repository` | ripgrep, with a Python fallback | Check usage before calling something a bug |
| `run_linter` | ruff, JSON output | Real diagnostics with real line numbers |
| `analyse_python` | AST structure | Complexity, nesting, bare excepts, mutable defaults |

### Path sandboxing

Every path-taking tool funnels through `resolve_in_root()`. Two details matter:

- It uses `Path.is_relative_to()`, **not `str.startswith()`**. A prefix check
  accepts `/repo-evil` for a root of `/repo`. There is a regression test for this.
- It calls `.resolve()`, which follows symlinks. A link inside the repo pointing
  at `/etc` resolves outside the root and is rejected by the same check as
  `../../etc` — no separate symlink handling needed.

Violations return `TOOL_ERROR: ...` to the model rather than raising. The agent
sees the observation and adapts; the run continues.

### Least privilege per role

| Agent | Tools |
|---|---|
| Reviewer | list, read, search, lint, ast |
| Verifier | read **only** |
| Q&A | read, get_findings |

The verifier's tool list is the interesting one. It gets enough to pull
surrounding context and no more — a verifier with a full toolbelt stops verifying
and starts re-reviewing, which duplicates cost and muddies the accountability of
each stage.

---

## 6. Memory

The assignment requires 上下文记忆. It is implemented at three levels, all
demonstrable:

1. **Within a run** — `ReviewState` is a `TypedDict` with `operator.add` reducers
   on the fields parallel branches write. `messages` uses `add_messages`.
2. **Across runs** — a `SqliteSaver` checkpointer keyed by `thread_id`. Kill the
   process, restart, and the thread resumes. This is what makes it a *feature*
   rather than a claim about an in-memory dict.
3. **Follow-up conversation** — `codereview ask` builds a separate Q&A agent that
   shares the checkpointer on a derived thread id (`<run-id>:qa`), seeded with the
   completed report. Asking "why is finding #3 a security issue?" in a new process
   works because the thread is on disk.

> **Reducer subtlety.** `candidates` has an `operator.add` reducer, so *returning*
> a value for it **appends** rather than replaces. A node that wants to replace the
> list must write to a different key. `verify` writes `verified`, which is
> deliberately unreduced. Getting this wrong duplicates every finding — there is a
> test for it (`test_parallel_branches_do_not_clobber_each_other`).

---

## 7. Reliability

Retries are layered but **deliberately not multiplied**. Three middleware attempts
inside three node attempts is nine API calls for one failure, which turns a
transient blip into a rate-limit ban.

| Layer | Mechanism | Scope |
|---|---|---|
| Middleware | `ModelRetryMiddleware`, `ToolRetryMiddleware` | Per model/tool call |
| Graph node | `RetryPolicy(max_attempts=2)` | Per node |
| Tool body | `subprocess` timeouts, `tenacity` | Infrastructure only |

`on_failure="continue"` is what makes "tool errors are fed back to the agent" real:
the error becomes a `ToolMessage` the model can read and react to, instead of an
exception that kills the run.

**Fail-fast cases.** A 401 (bad key), 402 (no balance), or 400 (bad model name) is
*not* retried. Retrying an exhausted account just burns time and hammers the API.
`codereview doctor` checks these up front.

**Branch isolation.** Each `review_file` invocation gets its own checkpointer
thread id (`<run-id>:<path>`). Sharing one thread across concurrent branches
corrupts the checkpoint — two branches would interleave writes to the same
conversation state.

---

## 8. The single-agent baseline

`--mode single` runs one ReAct loop over the whole repository with the same tools,
the same deterministic prefetch, and the same evidence binding. The only thing
removed is the architecture: no fan-out, no independent verifier.

It exists to make the architecture claim *testable*. The comparison isolates
structure, because everything else is held constant. Measurements are in
[`EVALUATION.md`](EVALUATION.md).

---

## 9. DeepSeek integration notes

These are easy to get wrong and were verified against the live API and the
installed packages.

| Issue | Detail |
|---|---|
| **Model name** | Use `deepseek-flash`. `deepseek-chat` and `deepseek-reasoner` are retired. LangChain's own integration page is stale here. |
| **Thinking mode and structured output are mutually exclusive** | DeepSeek returns `400 — Thinking mode does not support this tool_choice`. LangChain's `agents/factory.py` hardcodes `tool_choice="any"` whenever an agent has a structured-output tool, and there is no way to opt out. Thinking is therefore disabled for every role, not just the deterministic ones. See below. |
| **Structured output must use `ToolStrategy`** | Passing a bare Pydantic class lets the agent pick provider-native structured output, which DeepSeek rejects with `400 — This response_format type is unavailable now`. `ToolStrategy(Model)` fixes it. |
| **Thinking mode also ignores `temperature`** | `temperature` / `presence_penalty` / `frequency_penalty` are silently ignored while thinking is on, so `temperature=0` buys nothing. |
| **Use the provider package** | `langchain-deepseek`'s `ChatDeepSeek`, not `ChatOpenAI(base_url=...)`. The `langchain-openai` docstring explicitly says non-standard fields from third-party providers are not extracted. |
| **Custom parameters go in `extra_body`** | Not `model_kwargs`, which errors. |
| **Known limitation** | `langchain-deepseek` stores `reasoning_content` but does not re-send it across tool-calling turns, so chain-of-thought does not persist through a tool loop. Mitigated by capping tool calls per file and relying on deterministic tools for the facts. |
| **Unknown model name** | Made a config value plus a `doctor` round trip, so a rename fails loudly at startup rather than mid-review. |

### The thinking-mode tradeoff

This is the one place where the original design had to be given up.

The plan was to keep thinking on for the reviewer — it is the role where
reasoning should help most — and disable it only for the deterministic
structured-extraction roles. That is not possible. DeepSeek's thinking mode
rejects the forced `tool_choice="any"` that LangChain issues whenever an agent
carries a structured-output tool, and since `factory.py:1475` hardcodes it, the
choice is **thinking or structured output**, not both.

Structured output wins. Without it the findings cannot be parsed at all, and
falling back to parsing prose would be strictly less reliable than a schema.
Thinking is now disabled everywhere, which likely costs some detection quality on
subtle defects — an unquantified cost, and worth re-measuring if DeepSeek lifts
the restriction. `DISABLE_THINKING=false` is the switch.

The measured pipeline (100% recall on the fixture, 0 false positives on clean
code) is with thinking off, so the design works under the constraint rather than
in spite of it.

### Windows encoding

The console defaults to GBK on this machine. A single box-drawing character from
`rich`, or an emoji in model output, raises `UnicodeEncodeError` and kills the
run. `__main__.py` reconfigures `stdout`/`stderr` to UTF-8 before anything else,
and every file read specifies an encoding explicitly.

---

## 10. Failure modes that were fixed, not tolerated

Four defects found while building this are worth recording. Three were the kind
that produce a *plausible-looking wrong answer* rather than an error, and all
three were invisible to the offline test suite — which stubs the model out
entirely, and so never constructs a real agent, never issues a real request, and
never runs a real middleware stack.

### The ones a live API run was needed to find

**`ToolErrorMiddleware` with no handler.** `ToolErrorMiddleware()` constructs
successfully — its `on_error` argument defaults to `None` — and then raises
`ValueError: ToolErrorMiddleware requires 'on_error' and/or 'aon_error'` on the
*first invoke*. Construction-time checks would not have caught it; only running
the agent would. Now passed an explicit handler, with a test asserting the
handler is attached.

**Provider-native structured output.** Left to choose, `create_agent` selects the
provider's native `response_format`, which DeepSeek answers with
`400 — This response_format type is unavailable now`. Fixed by pinning
`ToolStrategy`.

**Thinking mode versus forced tool choice.** With the strategy fixed, the next
400 was `Thinking mode does not support this tool_choice`. Described in §9.

All three failed *every* file, so the run fell back to deterministic-only results
while still producing a complete-looking report. That is exactly the failure the
silent-degradation guard was built for, and it earned its keep on the first real
run: the banner said "the review did not run" instead of presenting 8 lint
findings as a finished review.

**A `NameError` in the error handler.** `ruff --fix` removed `import logging`
from `reviewer.py` at a point when it was genuinely unused. `_on_tool_error` was
added later and used `logger`, so the handler would have raised `NameError`
precisely when a tool failed — turning a recoverable tool error into a crashed
file. Caught by a unit test for the handler, and a reminder that an auto-fixer's
view of "unused" is only as good as the moment it runs.

### The two found offline

**Silent degradation without an API key.** Every LLM node caught its own failure
so that one bad file could not sink a run. The side effect was that a missing key
made every node fail, each failure was swallowed, and the run still produced a
report — built entirely from ruff and AST findings, looking exactly like a
complete review. A user would read it and believe the code had been reviewed.

Two changes fixed it, and neither is really about the key:

- A missing key is now a hard error, with the fix in the message.
- The only way to skip the LLM stages is an explicit `--offline`, which is
  labelled in the console, Markdown, JSON, and web outputs. Offline mode also
  refuses to schedule any review tasks, so it cannot start a model call by
  accident.

The general principle: **a partial result must be visibly partial.** Silently
returning less is worse than failing loudly, because the user cannot tell.

**Cross-source duplicate findings.** Ruff's `E722` and the AST tool's bare-except
check report the same line. Dedup matches on `(path, line range, category)`, so
they only merge if both are classified the same way — but the prefix mapping put
`E722` in "style" (it starts with `E`) while the AST tool called it a "bug". The
same defect appeared twice. Fixed with a per-rule override table in
`lint_tools.py`, which also corrects rules whose prefix gives the wrong severity.

This is why dedup keys on category rather than just location: two genuinely
different defects three lines apart must both survive. The cost is that the
category assignment has to be right, which is now enforced by an explicit table
rather than inferred from a rule's first letter.

---

## 11. Deliberate non-goals

- **No code execution.** `run_tests` was considered and dropped. Read-only tools
  mean the agent cannot damage the repository it is reviewing, which is worth more
  than the extra signal on an assignment. File reading and search already satisfy
  the tool requirement.
- **Python only.** A shallow pass over five languages would be worse than a deep
  pass over one. The AST tool and ruff are the reason Python findings can be
  grounded at all.
- **No auto-fix.** Suggestions show a concrete change but never write to the
  repository. Applying edits needs conflict handling and a rollback story, and the
  evidence trail is more valuable than the convenience.
