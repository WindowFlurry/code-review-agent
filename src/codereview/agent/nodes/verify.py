"""Verification: the reflection stage.

Every candidate that survived evidence binding is handed to an independent
adversarial agent whose only job is to refute it. Findings the verifier rejects
are kept in the state (so the report can show how many were caught) but excluded
from the report body.

Findings that came from ruff or the AST walk skip this stage entirely. A real
analyser reported them; there is no claim for a model to second-guess, and
letting the verifier reject a fact would be a downgrade.

Verification is the most expensive stage per finding -- one model call each --
so it runs on a bounded thread pool rather than sequentially.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ...config import get_settings
from ...errors import CodeReviewError
from ...models.findings import VerifiedFinding, VerifierVerdict
from ...tools.fs_tools import read_file
from ...tools.lint_tools import categorise
from ...tools.registry import ToolContext
from ..state import ReviewState, Usage

logger = logging.getLogger(__name__)

#: Lines of surrounding context given to the verifier on each side of a finding.
_CONTEXT_PAD = 25

#: Cache of the built verifier agent, keyed by repository root.
_VERIFIER_CACHE: dict[str, object] = {}


def _get_verifier(root: str):
    agent = _VERIFIER_CACHE.get(root)
    if agent is None:
        from ...agent.subagents.verifier import build_verifier

        settings = get_settings()
        ctx = ToolContext(root=Path(root).resolve(), settings=settings)
        agent = build_verifier(ctx, settings)
        _VERIFIER_CACHE[root] = agent
    return agent


def verify_node(state: ReviewState) -> dict:
    """Adjudicate every pending candidate."""
    settings = get_settings()
    root = state["repo_root"]
    candidates = state.get("candidates", [])

    pending = [c for c in candidates if c.verdict == "pending"]
    untouched = [c for c in candidates if c.verdict != "pending"]

    if not pending:
        # Everything was tool-sourced and already confirmed. Write `verified`
        # (unreduced), never `candidates` (reduced -- writing it would append).
        logger.info("verify: nothing to verify")
        return {"verified": untouched, "usage": []}

    logger.info("verify: adjudicating %d candidate(s)", len(pending))

    verified: list[VerifiedFinding] = []
    usage = Usage()

    workers = max(1, min(settings.max_concurrency, len(pending)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_verify_one, root, state["run_id"], finding): finding
            for finding in pending
        }
        for future in as_completed(futures):
            finding = futures[future]
            try:
                outcome, calls = future.result()
            except Exception as exc:  # noqa: BLE001 - never lose a finding to a crash
                logger.exception("verification crashed for %r", finding.title)
                outcome, calls = (
                    finding.model_copy(
                        update={
                            "verdict": "uncertain",
                            "verdict_reason": f"verification failed: {exc}",
                        }
                    ),
                    0,
                )
            verified.append(outcome)
            usage.llm_calls += calls

    confirmed = sum(1 for f in verified if f.verdict == "confirmed")
    rejected = sum(1 for f in verified if f.verdict == "rejected")
    uncertain = sum(1 for f in verified if f.verdict == "uncertain")
    logger.info(
        "verify: %d confirmed, %d uncertain, %d rejected", confirmed, uncertain, rejected
    )

    return {"verified": [*untouched, *verified], "usage": [usage]}


def _verify_one(root: str, run_id: str, finding: VerifiedFinding) -> tuple[VerifiedFinding, int]:
    """Verify a single finding. Returns the updated finding and the call count."""
    if finding.evidence is None:
        return (
            finding.model_copy(
                update={
                    "verdict": "rejected",
                    "verdict_reason": "no evidence to verify",
                    "rejected_by": "verifier",
                }
            ),
            0,
        )

    if finding.source in ("ruff", "ast"):
        return _crosscheck_tool_finding(finding)

    evidence = finding.evidence
    context = _context_for(root, evidence.path, evidence.line_start, evidence.line_end)

    from ...agent.subagents.verifier import build_verification_prompt

    prompt = build_verification_prompt(
        title=finding.title,
        category=finding.category,
        severity=finding.severity,
        rationale=finding.rationale,
        path=evidence.path,
        line_start=evidence.line_start,
        line_end=evidence.line_end,
        snippet=evidence.snippet,
        context=context,
    )

    agent = _get_verifier(root)
    result = agent.invoke(
        {"messages": [{"role": "user", "content": prompt}]},
        # One thread per finding; the verifier runs concurrently.
        config={"configurable": {"thread_id": f"{run_id}:verify:{evidence.path}:{evidence.line_start}"}},
    )

    calls = sum(1 for m in result.get("messages", []) if getattr(m, "usage_metadata", None))
    verdict = result.get("structured_response")

    if not isinstance(verdict, VerifierVerdict):
        try:
            verdict = VerifierVerdict.model_validate(verdict)
        except Exception:  # noqa: BLE001
            # An unparseable verdict must not silently confirm a finding.
            logger.warning("verifier returned no usable verdict for %r", finding.title)
            return (
                finding.model_copy(
                    update={
                        "verdict": "uncertain",
                        "verdict_reason": "verifier returned no usable verdict",
                    }
                ),
                calls,
            )

    update: dict = {"verdict": verdict.verdict, "verdict_reason": verdict.reason}
    if verdict.verdict == "rejected":
        update["rejected_by"] = "verifier"
    if verdict.corrected_severity:
        update["severity"] = verdict.corrected_severity

    return finding.model_copy(update=update), calls


def _crosscheck_tool_finding(finding: VerifiedFinding) -> tuple[VerifiedFinding, int]:
    """Confirm a tool-sourced finding without a model call.

    Cheap, and it keeps the report honest about provenance: these are marked
    confirmed because an analyser said so, not because a model agreed.
    """
    category, severity = categorise(finding.rule_id or "")
    return (
        finding.model_copy(
            update={
                "verdict": "confirmed",
                "verdict_reason": f"reported by {finding.rule_id or finding.source}",
                "severity": finding.severity if finding.rule_id else severity,
                "category": finding.category if finding.rule_id else category,
            }
        ),
        0,
    )


def _context_for(root: str, path: str, start: int, end: int) -> str:
    """Read a padded window around the finding for the verifier."""
    try:
        lo = max(1, start - _CONTEXT_PAD)
        return read_file(root, path, start_line=lo, end_line=end + _CONTEXT_PAD)
    except CodeReviewError as exc:
        logger.debug("could not read context for %s: %s", path, exc)
        return "(context unavailable)"
