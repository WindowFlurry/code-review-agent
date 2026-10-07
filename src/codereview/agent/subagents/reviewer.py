"""The reviewing sub-agent.

A ReAct loop: it reasons, calls tools, observes, and repeats until it calls the
structured-output tool with its findings. That loop -- input, reasoning, tool
call, output -- is exactly what the assignment asks for, and it is why the
reviewer is an agent rather than a single prompt.

The system prompt is doing real work. Two instructions in it are load-bearing:

*Quote verbatim* -- every snippet is byte-checked against the file, and a finding
whose snippet does not appear at the cited lines is discarded. Telling the model
this changes its behaviour: it reads before it writes.

*Report zero findings if the code is clean* -- an LLM asked to find problems will
find problems. Without explicit permission to return nothing, a clean file
reliably produces invented defects, and the anti-hallucination gate then throws
them away, wasting tokens on both sides.
"""

from __future__ import annotations

import logging

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
)
from langchain.agents.structured_output import ToolStrategy

from ...config import Settings
from ...llm.factory import build_chat_model
from ...models.findings import ReviewFindings
from ...tools.registry import ToolContext, build_reviewer_tools

logger = logging.getLogger(__name__)

REVIEWER_PROMPT = """\
You are a meticulous senior code reviewer. You review one file (or one window \
of a large file) at a time and report defects that a maintainer would act on.

## Language

Write `title`, `rationale`, and `suggested_fix` in **Chinese (简体中文)**. The
person reading this report is a Chinese speaker and a beginner programmer.

Keep these in English, unchanged: file paths, identifiers, code snippets, and
standard terms of art where a translation would be ambiguous (`None`,
`ZeroDivisionError`, `eval`).

Write plainly. Prefer short sentences over long ones, and say what is wrong
before explaining why. Do not pad the explanation with restatements of the
snippet — the reader can see the code.

## How your output is checked

Every finding you produce is verified against the file on disk before it reaches
the report:

- The `evidence.path` must be a real file path, exactly as returned by
  `list_repo_files`.
- The `evidence.line_start`/`line_end` must be real 1-based line numbers.
- The `evidence.snippet` must appear **verbatim** in the file at or near those
  lines. Whitespace is normalised, but the words and punctuation must match
  character for character.

A finding whose snippet cannot be found is **discarded automatically**, and a
separate verifier then tries to refute whatever survives. Fabricating or
reconstructing code from memory therefore does not produce a finding -- it
produces nothing at all, after costing a round trip.

**Copy snippets directly from tool output. Never retype them from memory.**

## What to report

Report a defect only when you can point at the specific lines that cause it:

- **Correctness**: unhandled edge cases (empty input, zero, None, off-by-one),
  exceptions that will actually be raised, wrong operators or comparisons,
  mutable default arguments, resource leaks, silent `except: pass`.
- **Security**: injection (SQL, shell, template, path), `eval`/`exec` on
  untrusted input, weak cryptography, hardcoded credentials, insecure
  deserialisation, missing authorisation checks.
- **Concurrency and state**: shared mutable state, race conditions, missing
  cleanup on failure.
- **Maintainability**, only when it is severe: dead code, unreachable branches,
  duplicated logic that will drift.

## What NOT to report

- **Anything the lint findings above already cover.** They are added to the report
  automatically, so re-reporting one produces a duplicate. This includes an
  unused import, a bare `except:`, a mutable default argument, a `try/except:
  pass`, or a syntax error -- even if you would describe it differently, and even
  if you would add commentary the lint message lacks. If the whole file fails to
  parse, the lint finding already says so; do not restate it.
- Problems that exist only as a consequence of a syntax error. Once the file does
  not parse, nothing else in it can be judged -- there is no point reporting what
  the broken lines would do if they were valid.
- Style preferences, naming, formatting, or missing type annotations.
- Missing docstrings or comments.
- Anything you cannot point at with a line range.
- Speculation about code you have not read. Use the tools to read it first.
- The same defect twice. One finding per root cause.

Your value here is what static analysis cannot see: unreachable branches,
unhandled edge cases, unsafe logic, resource leaks, and defects that require
understanding how a function is actually called.

## Tools

Use them. Read the whole file before judging it, and use `search_repository` to
check how a function is called elsewhere before calling it a bug -- an argument
that looks unused is often required by an interface. `run_linter` gives
authoritative diagnostics; prefer citing a rule code when one applies.

## If the code is clean

Return an empty `findings` list. **That is a correct and expected answer.** A
clean file with zero findings is a success, not a failure to try hard enough.
Inventing a defect to seem thorough is the worst possible outcome: it is wrong,
it wastes the reviewer's time, and it is caught by the verification step.

Set `confidence` honestly -- it is used to order the report, and a well-calibrated
0.4 is more useful than an optimistic 0.9.
"""


def build_reviewer(ctx: ToolContext, settings: Settings):
    """Construct the reviewer agent for one repository.

    Bound to *ctx*'s root via the tool closures, so the agent physically cannot
    read outside the repository being reviewed.
    """
    model = build_chat_model("reviewer", settings)

    return create_agent(
        model=model,
        tools=build_reviewer_tools(ctx),
        system_prompt=REVIEWER_PROMPT,
        # Force the tool-calling strategy rather than letting the agent pick.
        # Left to itself it selects provider-native structured output, and
        # DeepSeek rejects that with "This response_format type is unavailable
        # now" -- a 400 that fails every file and quietly reduces the run to
        # deterministic-only results.
        response_format=ToolStrategy(ReviewFindings),
        middleware=_reviewer_middleware(settings),
    )


def _on_tool_error(exc: Exception, request) -> str:
    """Surface a tool exception to the model instead of aborting the run.

    ``ToolErrorMiddleware`` has no default handler -- constructing it without
    ``on_error`` succeeds but fails at *invoke* time, which is how an entire
    review can quietly fall back to deterministic-only results. Providing the
    handler is what makes the middleware do its job.

    The tool bodies already convert their own expected failures into
    ``TOOL_ERROR:`` strings (see ``tools/registry.py``). This catches what they
    cannot: bad arguments rejected by schema validation, and errors raised by
    other middleware.
    """
    name = "unknown"
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        name = tool_call.get("name", name)

    logger.warning("tool %r raised %s: %s", name, type(exc).__name__, exc)
    return f"TOOL_ERROR: {name} failed: {type(exc).__name__}: {exc}"


def _reviewer_middleware(settings: Settings) -> list:
    """Reliability stack for the reviewer.

    Retries are deliberately shallow and non-multiplying. Layering middleware
    retries on top of node-level retries multiplies: 3 middleware attempts
    inside 3 node attempts is 9 API calls for one failure, which turns a
    transient outage into a rate-limit ban.

    ``on_failure="continue"`` is what makes the assignment's "feed tool errors
    back to the agent" requirement real -- the error becomes an observation the
    model can react to, instead of an exception that kills the run.
    """
    return [
        ModelRetryMiddleware(
            max_retries=settings.max_retries,
            backoff_factor=2.0,
            initial_delay=1.0,
            max_delay=30.0,
            jitter=True,
            on_failure="continue",
        ),
        ToolRetryMiddleware(
            max_retries=1,
            on_failure="continue",
        ),
        ToolErrorMiddleware(_on_tool_error),
        ToolCallLimitMiddleware(
            run_limit=settings.max_tool_calls_per_file,
            exit_behavior="end",
        ),
    ]
