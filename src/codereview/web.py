"""Optional Streamlit interface.

Run with::

    python -m streamlit run src/codereview/web.py

A thin shell over the same graph the CLI uses -- no logic is duplicated, so the
two interfaces cannot drift apart. The findings table is rendered directly from
the report model, which means a finding that would be rejected by the CLI's
evidence check is not silently shown here as if it were real.
"""

from __future__ import annotations

import sys
from pathlib import Path

# When Streamlit runs this file directly, the package directory is not on the
# path. Add the `src/` root so `import codereview` resolves.
_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import streamlit as st  # noqa: E402

from codereview import __version__  # noqa: E402
from codereview.config import get_settings  # noqa: E402
from codereview.runner import run_review  # noqa: E402

_SEVERITY_COLOUR = {
    "critical": "#d32f2f",
    "high": "#f57c00",
    "medium": "#fbc02d",
    "low": "#0288d1",
    "info": "#757575",
}


def main() -> None:
    st.set_page_config(page_title="Code Review Agent", page_icon="🔍", layout="wide")

    st.title("🔍 Code Review Agent")
    st.caption(
        "LangGraph pipeline · every finding is checked against the file on disk "
        f"before it is reported · v{__version__}"
    )

    settings = get_settings()

    with st.sidebar:
        st.header("Settings")
        st.write(f"**Model**: `{settings.deepseek_model}`")
        st.write(f"**Base URL**: `{settings.deepseek_base_url}`")

        if not settings.has_api_key:
            st.warning(
                "No API key — only offline mode is available. Copy `.env.example` "
                "to `.env` and set `DEEPSEEK_API_KEY` for a full review."
            )

        offline = st.checkbox(
            "Offline mode",
            value=not settings.has_api_key,
            disabled=not settings.has_api_key,
            help="Deterministic analysis only (ruff + AST). No model calls.",
        )

        mode = st.radio(
            "Mode",
            ["graph", "single"],
            captions=[
                "Full pipeline: per-file review + independent verification",
                "Baseline: one ReAct agent, no verification",
            ],
            disabled=offline,
        )

        formats = st.multiselect(
            "Report formats", ["md", "json"], default=["md", "json"]
        )

    target = st.text_input("Repository path", value=".", help="Path to the code to review")

    can_run = settings.has_api_key or offline
    if st.button("Run review", type="primary", disabled=not can_run):
        _run(target, mode, formats, offline)


def _run(target: str, mode: str, formats: list[str], offline: bool = False) -> None:
    path = Path(target).expanduser()
    if not path.exists():
        st.error(f"No such path: {path}")
        return

    with st.status("Reviewing…", expanded=True) as status:
        st.write("Inventorying the repository and running the deterministic analysers…")
        try:
            report = run_review(
                target=path,
                mode=mode,
                formats=formats,
                offline=offline,
                settings=get_settings(),
            )
        except Exception as exc:  # noqa: BLE001 - surface the error in the UI
            status.update(label="Review failed", state="error")
            st.exception(exc)
            return
        status.update(label="Review complete", state="complete")

    if report.aborted:
        st.warning(f"Nothing to review: {report.abort_reason}")
        return

    _render_report(report)


def _render_report(report) -> None:
    stats = report.stats
    findings = report.reportable

    columns = st.columns(4)
    columns[0].metric("Findings", len(findings))
    columns[1].metric("Files reviewed", stats.files_reviewed)
    columns[2].metric("Rejected", stats.total_rejected)
    columns[3].metric("Duration", f"{stats.duration_s:.0f}s")

    if report.offline:
        st.warning(
            "**Deterministic analysis only.** Ruff and the AST analyser ran; the LLM "
            "reviewer and verifier did not. Defects that require reading the code — "
            "missing edge-case handling, unsafe logic — are not covered."
        )

    if stats.rejected_by_evidence:
        st.info(
            f"{stats.rejected_by_evidence} candidate finding(s) cited code that does not "
            "exist in the file and were discarded before reaching this report."
        )

    if report.summary:
        st.markdown(f"**Summary.** {report.summary}")

    if not findings:
        st.success(
            "No issues found. This is a real result — the reviewer is instructed to "
            "report nothing rather than invent a defect."
        )
    else:
        for index, finding in enumerate(findings, start=1):
            _render_finding(index, finding)

    with st.expander("Run statistics"):
        st.json(stats.model_dump(mode="json"))

    if report.errors:
        with st.expander(f"{len(report.errors)} file(s) could not be reviewed"):
            for error in report.errors:
                st.text(error)


def _render_finding(index: int, finding) -> None:
    colour = _SEVERITY_COLOUR.get(finding.severity, "#757575")
    evidence = finding.evidence

    location = "(no location)"
    if evidence:
        location = f"{evidence.path}:{evidence.line_start}"
        if evidence.line_end != evidence.line_start:
            location += f"-{evidence.line_end}"

    st.markdown(
        f"<span style='background:{colour};color:white;padding:2px 8px;"
        f"border-radius:4px;font-size:0.75em;font-weight:600'>"
        f"{finding.severity.upper()}</span> "
        f"**{index}. {finding.title}**",
        unsafe_allow_html=True,
    )
    st.caption(f"`{location}` · {finding.category} · source: {finding.rule_id or finding.source}")
    st.write(finding.rationale)

    if evidence and evidence.snippet:
        suffix = evidence.path.rsplit(".", 1)[-1] if "." in evidence.path else "text"
        st.code(evidence.snippet, language=suffix)

    if finding.suggested_fix:
        st.success(f"**Suggested fix:** {finding.suggested_fix}")

    st.divider()


if __name__ == "__main__" or st.runtime.exists():
    main()
