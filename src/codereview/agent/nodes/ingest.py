"""Deterministic prefetch: inventory the repo, then run the cheap analysers.

Both nodes run before the first LLM call. Everything they produce is ground
truth -- real line numbers from real tools -- which is what makes the model's
claims checkable later. Doing this first also means the planner can be told what
it is choosing between, instead of guessing at the repository's contents.
"""

from __future__ import annotations

import logging

from ...config import get_settings
from ...errors import CodeReviewError
from ...ingestion import walk_repo
from ...tools.ast_tools import python_ast_summary
from ...tools.lint_tools import run_lint
from ..state import ReviewState

logger = logging.getLogger(__name__)


def ingest_node(state: ReviewState) -> dict:
    """Walk the repository and record what is reviewable.

    Never raises on a bad repository: an empty or missing repo sets
    ``abort_reason`` and the graph short-circuits to the report, so the user gets
    a clear explanation instead of a traceback.
    """
    settings = get_settings()
    root = state["repo_root"]

    try:
        files = walk_repo(
            root,
            max_files=settings.max_files,
            max_file_bytes=settings.max_file_bytes,
            languages=("python",),
            only_files=state.get("only_files") or None,
        )
    except CodeReviewError as exc:
        logger.error("ingestion failed: %s", exc)
        return {"files": [], "abort_reason": f"could not read repository: {exc}"}

    reviewable = [f for f in files if f.reviewable]
    skipped = [f for f in files if f.skipped]

    logger.info(
        "ingest: %d reviewable, %d skipped", len(reviewable), len(skipped)
    )

    if not reviewable:
        reasons: dict[str, int] = {}
        for meta in skipped:
            key = meta.skip_reason or "unknown"
            reasons[key] = reasons.get(key, 0) + 1
        detail = ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
        return {
            "files": files,
            "abort_reason": (
                f"no reviewable Python files found ({detail})" if skipped else
                "no Python files found in this repository"
            ),
        }

    return {"files": files, "abort_reason": None}


def route_after_ingest(state: ReviewState) -> str:
    """Short-circuit straight to the report when there is nothing to review."""
    return "report" if state.get("abort_reason") else "static_scan"


def static_scan_node(state: ReviewState) -> dict:
    """Run ruff and the AST analyser over every reviewable file.

    Both are best-effort: a repo with no ruff installed still gets an AST pass,
    and a file that will not parse is recorded rather than skipped, because a
    syntax error is itself the most important finding in the file.
    """
    settings = get_settings()
    root = state["repo_root"]
    files = [f for f in state.get("files", []) if f.reviewable]

    # Scope ruff to the files actually being reviewed. Without this a single-file
    # review lints the file's entire parent directory, which is both slow and
    # wrong: the report fills with defects from code the user did not ask about.
    lint_paths: list[str] | None = None
    if state.get("only_files"):
        lint_paths = list(state["only_files"])
    elif files:
        lint_paths = [meta.path for meta in files]

    lint_by_file: dict[str, list] = {}
    try:
        findings = run_lint(root, lint_paths)
        for finding in findings:
            lint_by_file.setdefault(finding.path, []).append(finding)
        logger.info("static_scan: ruff reported %d finding(s)", len(findings))
    except CodeReviewError as exc:
        logger.warning("ruff unavailable, continuing without lint: %s", exc)

    ast_by_file: dict[str, object] = {}
    for meta in files:
        try:
            ast_by_file[meta.path] = python_ast_summary(
                root, meta.path, max_bytes=settings.max_file_bytes
            )
        except CodeReviewError as exc:
            logger.debug("ast failed for %s: %s", meta.path, exc)

    logger.info("static_scan: analysed %d file(s)", len(ast_by_file))

    return {"lint_by_file": lint_by_file, "ast_by_file": ast_by_file}
