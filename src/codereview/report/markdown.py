"""Markdown 报告——可以直接当作业交付物的那份。"""

from __future__ import annotations

from pathlib import Path

from ..models.findings import VerifiedFinding
from ..models.report import ReviewReport
from . import labels

_SEVERITY_BADGE = {
    "critical": "🟥",
    "high": "🟧",
    "medium": "🟨",
    "low": "🟦",
    "info": "⬜",
}


def render_markdown(report: ReviewReport) -> str:
    """把报告渲染成 Markdown 文本。"""
    lines: list[str] = []

    lines.append("# 代码审查报告")
    lines.append("")
    lines.append(f"- **被检查的代码**：`{report.repo_root}`")
    if report.stats.finished_at:
        lines.append(f"- **完成时间**：{report.stats.finished_at:%Y-%m-%d %H:%M:%S}")
    if report.stats.duration_s:
        lines.append(f"- **耗时**：{report.stats.duration_s:.1f} 秒")
    lines.append("")

    if report.offline:
        lines.append(
            "> ⚠️ **本次只做了确定性检查。** ruff 静态检查和语法结构分析已运行，"
            "AI 阅读与复核没有执行。需要读懂代码才能发现的问题（缺少边界处理、"
            "逻辑不安全）**不在**本报告范围内。"
        )
        lines.append("")

    if report.aborted:
        lines.append("> **没有可检查的内容。** " + (report.abort_reason or ""))
        lines.append("")
        lines.append(_render_stats(report))
        return "\n".join(lines)

    if report.summary:
        lines.append("## 摘要")
        lines.append("")
        lines.append(report.summary)
        lines.append("")

    findings = report.reportable
    lines.append("## 发现的问题")
    lines.append("")

    if not findings:
        lines.append("未发现问题。")
        lines.append("")
        lines.append(
            "助手被明确要求：宁可什么都不报，也不许编造问题。"
            "并且每条结论在写进报告之前，都会拿真实文件核对一遍代码。"
        )
        lines.append("")
    else:
        for index, finding in enumerate(findings, start=1):
            lines.extend(_render_finding(index, finding))

    if report.errors:
        lines.append("## 未能检查的文件")
        lines.append("")
        for error in report.errors[:20]:
            lines.append(f"- `{error}`")
        lines.append("")

    lines.append(_render_stats(report))
    return "\n".join(lines)


def _render_finding(index: int, finding: VerifiedFinding) -> list[str]:
    evidence = finding.evidence
    badge = _SEVERITY_BADGE.get(finding.severity, "")

    lines = [f"### {index}. {badge} {finding.title}", ""]

    location = "_（未标注位置）_"
    if evidence:
        location = f"`{evidence.path}:{evidence.line_start}"
        if evidence.line_end != evidence.line_start:
            location += f"-{evidence.line_end}"
        location += "`"

    meta = [
        f"**严重程度**：{labels.severity(finding.severity)}",
        f"**类别**：{labels.category(finding.category)}",
        f"**位置**：{location}",
    ]
    if finding.source != "llm":
        meta.append(f"**来源**：{labels.source(finding.source)}")
    if finding.verdict == "uncertain":
        meta.append("**状态**：已报告，但未获复核确认")
    lines.append(" | ".join(meta))
    lines.append("")

    lines.append(finding.rationale)
    lines.append("")

    if evidence and evidence.snippet:
        suffix = evidence.path.rsplit(".", 1)[-1] if "." in evidence.path else ""
        lines.append(f"```{suffix}")
        lines.append(evidence.snippet)
        lines.append("```")
        lines.append("")

    if finding.suggested_fix:
        lines.append("**修改建议**")
        lines.append("")
        lines.append("```")
        lines.append(finding.suggested_fix)
        lines.append("```")
        lines.append("")

    return lines


def _render_stats(report: ReviewReport) -> str:
    """运行统计——证明程序里的各种保护确实跑了。"""
    stats = report.stats
    lines = ["## 本次运行统计", "", "| 项目 | 数值 |", "|---|---|"]
    lines.append(f"| 已检查文件 | {stats.files_reviewed} |")
    lines.append(f"| 跳过文件 | {stats.files_skipped} |")
    lines.append(f"| 报告的问题 | {len(report.reportable)} |")
    lines.append(f"| AI 提出的候选 | {stats.candidates} |")
    lines.append(f"| 已确认 | {stats.confirmed} |")
    lines.append(f"| 因证据对不上被丢弃 | {stats.rejected_by_evidence} |")
    lines.append(f"| 因复核不通过被否决 | {stats.rejected_by_verifier} |")
    lines.append(f"| AI 调用次数 | {stats.llm_calls} |")
    lines.append(f"| 工具调用次数 | {stats.tool_calls} |")

    if stats.files_failed:
        lines.append(f"| 检查失败的文件 | {stats.files_failed} |")
    if stats.malformed_responses:
        lines.append(f"| AI 回复格式错误 | {stats.malformed_responses} |")

    total_in = stats.prompt_cache_hit_tokens + stats.prompt_cache_miss_tokens
    if total_in:
        pct = stats.prompt_cache_hit_tokens / total_in * 100
        lines.append(f"| 输入令牌 | {total_in:,}（缓存命中 {pct:.0f}%）|")
        lines.append(f"| 输出令牌 | {stats.output_tokens:,} |")

    lines.append("")
    return "\n".join(lines)


def write_markdown(report: ReviewReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")
    return path
