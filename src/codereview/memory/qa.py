"""Follow-up Q&A about a completed review.

This is the part of the memory requirement that is unambiguous: the assignment
asks for 上下文记忆, and a conversation that can still answer "why is finding 3 a
security issue?" -- in a *new process*, after the review finished -- is a
concrete demonstration of it rather than a claim about a state dictionary.

The agent shares the review's checkpointer but runs on a derived thread id, so
the review's own checkpoint is never appended to and a review can be re-run
without clobbering its conversation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import get_settings
from ..tools.registry import ToolContext, build_qa_tools
from .checkpointer import get_checkpointer

logger = logging.getLogger(__name__)

QA_PROMPT = """\
You answer questions about a code review that already ran.

The review's findings are available via the `get_findings` tool. Read them \
before answering rather than relying on memory.

Rules:

- Cite `path:line` for anything you claim about the code.
- If the review did not cover something or did not report a defect the user asks
  about, say so plainly. Do not speculate that "it probably has an issue".
- If the user challenges a finding, re-read the code with `read_source` and
  either defend it with the actual lines or concede that it looks wrong.
- Keep answers short. The user has already read the report.
"""


def _default_thread(run_id: str) -> str:
    return f"{run_id}:qa"


def build_qa_agent(
    repo_root: Path | str,
    report_json: str,
    *,
    checkpointer=None,
    settings=None,
):
    """Build the follow-up agent, seeded with a completed review."""
    from langchain.agents import create_agent

    from ..llm.factory import build_chat_model

    settings = settings or get_settings()
    ctx = ToolContext(root=Path(repo_root).resolve(), settings=settings)

    return create_agent(
        model=build_chat_model("qa", settings),
        tools=build_qa_tools(ctx, report_json),
        system_prompt=QA_PROMPT,
        checkpointer=checkpointer,
    )


def _load_report(run_dir: Path) -> tuple[str, str]:
    """Read a completed run's JSON report. Returns ``(repo_root, json_text)``."""
    report_path = run_dir / "report.json"
    if not report_path.is_file():
        raise FileNotFoundError(
            f"no report at {report_path}. Run `codereview review` first, or pass --run-dir."
        )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    return payload.get("repo_root", "."), report_path.read_text(encoding="utf-8")


def ask_question(
    *,
    thread_id: str,
    question: str,
    run_dir: Path | None = None,
    repo_root: Path | None = None,
) -> str:
    """Ask one question about a previous review.

    Args:
        thread_id: Thread id of the review (``codereview review --thread X``).
        question: The question to answer.
        run_dir: Directory containing ``report.json`` from the review.
        repo_root: Overrides the repository path recorded in the report.
    """
    settings = get_settings()
    run_dir = run_dir or _latest_run_dir(settings.run_dir, thread_id)
    recorded_root, report_json = _load_report(run_dir)
    root = repo_root or Path(recorded_root)

    # Say which review is being answered about. A follow-up question answered
    # against the wrong run reads as authoritative and is about different code,
    # so the caller needs to be able to see the target.
    logger.info("answering about review in %s (%s)", run_dir, recorded_root)

    db_path = run_dir / "threads.sqlite"
    checkpointer = get_checkpointer(db_path)

    agent = build_qa_agent(root, report_json, checkpointer=checkpointer, settings=settings)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"configurable": {"thread_id": _default_thread(thread_id)}},
    )

    messages = result.get("messages", [])
    if not messages:
        return "(no answer produced)"

    content = getattr(messages[-1], "content", "")
    if isinstance(content, list):
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)


def run_repl(*, thread_id: str, console, run_dir: Path | None = None) -> None:
    """Interactive follow-up session.

    The agent is built once and reused, so the checkpointer accumulates the
    conversation and ``it`` resolves against earlier turns.
    """
    settings = get_settings()
    run_dir = run_dir or _latest_run_dir(settings.run_dir, thread_id)
    recorded_root, report_json = _load_report(run_dir)

    checkpointer = get_checkpointer(run_dir / "threads.sqlite")
    agent = build_qa_agent(Path(recorded_root), report_json, checkpointer=checkpointer)

    console.print(f"[dim]review under discussion: {run_dir}[/dim]")
    console.print(f"[dim]repository: {recorded_root}[/dim]")
    console.print("[bold]Ask about the review.[/bold] [dim]Empty line or Ctrl-C exits.[/dim]\n")

    config = {"configurable": {"thread_id": _default_thread(thread_id)}}

    while True:
        try:
            question = console.input("[bold cyan]> [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return

        if not question:
            return

        try:
            result = agent.invoke(
                {"messages": [{"role": "user", "content": question}]}, config
            )
            messages = result.get("messages", [])
            answer = getattr(messages[-1], "content", "") if messages else ""
            console.print(f"\n{answer}\n")
        except Exception as exc:  # noqa: BLE001 - a bad turn should not end the session
            console.print(f"[red]error:[/red] {exc}\n")


def _latest_run_dir(base: Path, thread_id: str | None = None) -> Path:
    """Find the run to answer questions about.

    Prefers a run whose thread id matches, then the most recently *modified* run
    with a report. Modification time rather than name: run directories can be
    given explicit names (``--out runs/security-audit``), and those sort against
    timestamped ones in ways that have nothing to do with recency.

    Raises rather than falling back to an older run. Silently answering questions
    about a *different* review is worse than refusing -- the answer looks
    authoritative and is about the wrong code.
    """
    if not base.is_dir():
        raise FileNotFoundError(f"no runs directory at {base}; run a review first")

    completed = [
        d
        for d in base.iterdir()
        if d.is_dir() and (d / "report.json").is_file()
    ]
    if not completed:
        raise FileNotFoundError(f"no completed review found under {base}")

    if thread_id:
        matching = [d for d in completed if d.name.endswith(f"-{thread_id}") or d.name == thread_id]
        if matching:
            completed = matching

    return max(completed, key=lambda d: (d / "report.json").stat().st_mtime)
