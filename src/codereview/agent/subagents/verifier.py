"""The verification sub-agent -- the reflection step.

This is the *generator-verifier* pattern: a second agent, with a different
prompt and a different job, whose only task is to try to falsify what the first
one claimed. A model asked "is this finding correct?" tends to agree; a model
asked "refute this, and default to rejected" does not.

The verifier receives the finding plus the real file content around it. It
cannot see the reviewer's reasoning, so it judges the claim on the code alone.

It is the second of two gates. The first -- evidence binding -- is deterministic
and already ran: any finding reaching this point has had its snippet matched
byte-for-byte against the file. So this stage is not asking "is the code real?"
but "does the real code actually have this problem?"
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy

from ...config import Settings
from ...llm.factory import build_chat_model
from ...models.findings import VerifierVerdict
from ...tools.registry import ToolContext, build_verifier_tools

VERIFIER_PROMPT = """\
You are an adversarial code-review verifier. Another reviewer has reported a \
defect. Your job is to decide whether the claim is actually true of this code.

Write your `reason` in **Chinese (简体中文)**, in one or two short sentences.
Keep code, paths and identifiers in English.

## Your default is `rejected`

Report `confirmed` only when the code in front of you clearly demonstrates the
defect. If the claim is plausible but unproven, if it depends on assumptions
about code you cannot see, or if it is a matter of taste rather than a real
problem -- answer `rejected`.

A false positive costs the author real time and erodes trust in the whole
review. A missed nit costs almost nothing. When you are genuinely torn, use
`uncertain` -- it is reported to the human as unproven, which is honest.

## How to judge

- Read the cited lines. Does the problem exist *there*?
- Is the claimed failure actually reachable? A branch that can never execute is
  not a bug.
- Is it already handled? Look for a guard, a try/except, or a caller that
  validates first. Use `read_source` to check callers if the claim depends on
  how the function is used.
- Is the severity right? A style issue is not `critical`. Correct it via
  `corrected_severity` if it is clearly wrong.
- Reject claims that describe what the code *is* rather than a problem with it.

## Answer directly

Do not rewrite the code, do not suggest a better design, and do not raise
defects of your own. One verdict, one justification.
"""


def build_verifier(ctx: ToolContext, settings: Settings):
    """Construct the verification agent.

    Gets only ``read_source`` -- enough to pull surrounding context, not enough
    to go re-run the whole review. A verifier with a full toolbelt stops
    verifying and starts reviewing.
    """
    model = build_chat_model("verifier", settings)

    return create_agent(
        model=model,
        tools=build_verifier_tools(ctx),
        system_prompt=VERIFIER_PROMPT,
        # Same reason as the reviewer: DeepSeek rejects provider-native
        # structured output, so pin the tool-calling strategy.
        response_format=ToolStrategy(VerifierVerdict),
        middleware=[
            ModelRetryMiddleware(
                max_retries=settings.max_retries,
                backoff_factor=2.0,
                on_failure="continue",
            ),
            # A verifier needs at most a couple of reads. Capping hard keeps a
            # confused verifier from looping on one finding.
            ToolCallLimitMiddleware(run_limit=4, exit_behavior="end"),
        ],
    )


def build_verification_prompt(
    *,
    title: str,
    category: str,
    severity: str,
    rationale: str,
    path: str,
    line_start: int,
    line_end: int,
    snippet: str,
    context: str,
) -> str:
    """Assemble the per-finding verification request.

    The surrounding context is included so most verdicts need no tool call at
    all -- cheaper, faster, and it keeps the verifier anchored to the lines
    under discussion.
    """
    return f"""\
# Claim under review

**Title**: {title}
**Category**: {category}
**Claimed severity**: {severity}
**Location**: {path}:{line_start}-{line_end}

## Reviewer's rationale

{rationale}

## The cited code, exactly as it appears on disk

```
{snippet}
```

## Surrounding context (`{path}`, lines {line_start}-{line_end} widened)

```
{context}
```

Decide: does this code actually have the claimed defect? Answer `rejected`
unless the code above clearly proves it.
"""
