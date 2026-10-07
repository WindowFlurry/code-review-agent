"""LangChain tool wrappers, bound to a repository root.

Three rules shape this module:

1. **Tools never raise at the agent.** An exception is converted into a readable
   ``TOOL_ERROR:`` string, which the agent sees as an observation and can react
   to -- retry with a different path, pick another tool, or give up gracefully.
   A raised exception would instead abort the whole review over one bad guess.

2. **Authority is granted per role.** The reviewer gets exploration tools; the
   verifier gets only what it needs to check a claim. Least privilege is a
   deliberate design decision, not an accident of wiring.

3. **Every result is bounded.** Caps on entries, bytes, and matches keep a single
   tool call from consuming the model's entire context window.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from langchain.tools import tool

from ..config import Settings, get_settings
from ..errors import CodeReviewError
from .ast_tools import python_ast_summary
from .fs_tools import list_files, read_file
from .lint_tools import run_lint
from .search_tools import format_matches, search_code

logger = logging.getLogger(__name__)

#: Cap on bytes returned by any single read, independent of the settings cap.
_MAX_TOOL_OUTPUT_CHARS = 40_000


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything the tools need that the model must not be able to change."""

    root: Path
    settings: Settings
    allow_exec: bool = False
    #: When reviewing a single file, the tools that default to "the whole
    #: repository" must be narrowed to it. Otherwise `run_linter()` with no
    #: arguments lints the file's entire parent directory -- which for a file on
    #: the Desktop means every project on it.
    restrict_to: tuple[str, ...] | None = None


def _guard(fn, *args, **kwargs) -> str:
    """Run a tool body, converting failures into agent-readable text.

    The distinction that matters: a ``SandboxViolation`` or ``ToolError`` is
    *expected* -- the model asked for something out of bounds -- so it becomes an
    observation. Anything else is a genuine defect and is logged with a
    traceback before being surfaced, because silently swallowing it would hide
    bugs in the reviewer's own tooling.
    """
    try:
        return fn(*args, **kwargs)
    except CodeReviewError as exc:
        logger.debug("tool reported a handled error: %s", exc)
        return f"TOOL_ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001 - last line of defence before the agent
        logger.exception("unexpected failure in a tool")
        return f"TOOL_ERROR: unexpected failure: {type(exc).__name__}: {exc}"


def _truncate(text: str, limit: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n[TRUNCATED: output exceeded {limit} characters]"


def build_reviewer_tools(ctx: ToolContext) -> list:
    """Tools for the reviewing sub-agent: full read-only exploration."""
    root = ctx.root
    settings = ctx.settings

    @tool
    def list_repo_files(subdir: str = ".", glob: str = "*.py") -> str:
        """List files in the repository so you never have to guess a path.

        Use this before citing a file. Paths returned here are the exact strings
        to put in the `evidence.path` field.

        Args:
            subdir: Directory to list, relative to the repository root.
            glob: Filename filter, for example "*.py".
        """
        def _run() -> str:
            if ctx.restrict_to and subdir == ".":
                return "".join(f"{name}\n" for name in ctx.restrict_to)
            listing = list_files(root, subdir=subdir, glob=glob, max_entries=300)
            return "".join(f"{name}\n" for name in listing) or "(no files)"

        return _guard(_run)

    @tool
    def read_source(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        """Read a source file with line numbers.

        Always read a file before reporting a defect in it. Copy the returned
        lines verbatim into `evidence.snippet` -- do not paraphrase or retype
        from memory, because the snippet is checked against the file on disk.

        Args:
            path: Repository-relative path, as returned by list_repo_files.
            start_line: First line to read (1-based). Omit to start at the top.
            end_line: Last line to read, inclusive. Omit to read to the end.
        """
        return _guard(
            lambda: _truncate(
                read_file(
                    root,
                    path,
                    start_line=start_line,
                    end_line=end_line,
                    max_bytes=settings.max_file_bytes,
                )
            )
        )

    @tool
    def search_repository(pattern: str, glob: str = "*.py") -> str:
        """Search the repository for a regular expression.

        Use this to check how something is *used* before calling it a bug. A
        parameter that looks unused is often required by an interface; a function
        that looks unsafe may be guarded by every caller.

        Args:
            pattern: Regular expression, for example "def open_session\\(".
            glob: Filename filter.
        """
        return _guard(
            lambda: _truncate(
                format_matches(search_code(root, pattern, glob=glob, max_results=50))
            )
        )

    @tool
    def run_linter(paths: list[str] | None = None) -> str:
        """Run ruff and return its findings as JSON.

        These are real diagnostics from a real analyser, so a finding backed by
        this is trustworthy. Prefer quoting a lint rule when one applies.

        Args:
            paths: Files or directories to lint. Omit to lint the whole repository.
        """
        def _run() -> str:
            # Fall back to the scoped file set, never to "everything".
            effective = paths or (list(ctx.restrict_to) if ctx.restrict_to else None)
            findings = run_lint(root, effective)
            if not findings:
                return "(no lint issues)"
            payload = [
                {
                    "rule": f.rule_id,
                    "path": f.path,
                    "line": f.line_start,
                    "message": f.message,
                }
                for f in findings[:200]
            ]
            return _truncate(json.dumps(payload, ensure_ascii=False, indent=1))

        return _guard(_run)

    @tool
    def analyse_python(path: str) -> str:
        """Return structural facts about a Python file.

        Reports real function boundaries, cyclomatic complexity, nesting depth,
        bare `except:` clauses, and mutable default arguments. A file that does
        not parse is reported as such -- that is itself a defect worth reporting.

        Args:
            path: Repository-relative path to a Python file.
        """
        def _run() -> str:
            summary = python_ast_summary(root, path, max_bytes=settings.max_file_bytes)
            return _truncate(json.dumps(summary.model_dump(), ensure_ascii=False, indent=1))

        return _guard(_run)

    return [list_repo_files, read_source, search_repository, run_linter, analyse_python]


def build_verifier_tools(ctx: ToolContext) -> list:
    """Tools for the verification sub-agent.

    Deliberately minimal: ``read_source`` lets the verifier pull surrounding
    context, but it gets no search and no lint. A verifier that can go exploring
    drifts into re-doing the review instead of judging the claim in front of it.
    """
    root = ctx.root
    settings = ctx.settings

    @tool
    def read_source(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        """Read a source file with line numbers, to check a specific claim.

        Args:
            path: Repository-relative path.
            start_line: First line to read (1-based).
            end_line: Last line to read, inclusive.
        """
        return _guard(
            lambda: _truncate(
                read_file(
                    root,
                    path,
                    start_line=start_line,
                    end_line=end_line,
                    max_bytes=settings.max_file_bytes,
                ),
                12_000,
            )
        )

    return [read_source]


def build_qa_tools(ctx: ToolContext, report_json: str):
    """Tools for the follow-up Q&A agent.

    The report is injected as tool output rather than baked into the system
    prompt, so answering "what did you find?" does not depend on the model
    remembering a large JSON blob token-for-token.
    """
    root = ctx.root
    settings = ctx.settings

    @tool
    def get_findings() -> str:
        """Return the complete list of findings from the review that was just run."""
        return _truncate(report_json, 30_000)

    @tool
    def read_source(path: str, start_line: int | None = None, end_line: int | None = None) -> str:
        """Read a source file with line numbers.

        Args:
            path: Repository-relative path.
            start_line: First line to read (1-based).
            end_line: Last line to read, inclusive.
        """
        return _guard(
            lambda: _truncate(
                read_file(
                    root,
                    path,
                    start_line=start_line,
                    end_line=end_line,
                    max_bytes=settings.max_file_bytes,
                ),
                12_000,
            )
        )

    return [get_findings, read_source]


def default_context(root: Path | str, *, allow_exec: bool = False) -> ToolContext:
    return ToolContext(root=Path(root).resolve(), settings=get_settings(), allow_exec=allow_exec)
