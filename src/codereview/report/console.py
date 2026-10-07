"""终端报告（中文）。

页脚那一块最重要。它报告的**不是**发现了什么，而是**否决了什么、跳过了
什么**。"这份报告来自确定性检查，AI 没有阅读代码" 这类话，比在 README 里
写一百句"很可靠"都有用——因为它由产生结论的同一段代码打印出来。
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..models.findings import SEVERITY_ORDER, VerifiedFinding
from ..models.report import SEVERITY_COLOURS, ReviewReport
from . import labels

_RULE = "─" * 60


def render_console(report: ReviewReport, console: Console | None = None) -> None:
    """把完整报告打印到终端。"""
    console = console or Console()

    _render_header(report, console)

    if report.offline:
        console.print(
            Panel(
                "[yellow]本次只做了确定性检查。[/yellow]\n\n"
                "运行了 ruff 静态检查和语法结构分析，AI 阅读与复核均未执行。\n"
                "需要读懂代码才能发现的问题（缺少边界处理、逻辑不安全）"
                "**不在**本次报告范围内。",
                border_style="yellow",
                title="[yellow]部分检查[/yellow]",
            )
        )
        console.print()

    if report.aborted:
        console.print(
            Panel(
                _abort_message(report.abort_reason),
                title="[yellow]没有可检查的内容[/yellow]",
                border_style="yellow",
            )
        )
        _render_footer(report, console)
        return

    findings = report.reportable
    if not findings:
        console.print(
            Panel(
                "[green]未发现问题。[/green]\n\n"
                "这是一个真实结果，不是程序没干活。助手被明确要求："
                "宁可什么都不报，也不许编造问题。",
                border_style="green",
                title="[green]检查通过[/green]",
            )
        )
    else:
        for index, finding in enumerate(findings, start=1):
            _render_finding(index, finding, console)

    _render_errors(report, console)
    _render_footer(report, console)


def _abort_message(reason: str | None) -> str:
    """把内部的终止原因翻成人话。"""
    if not reason:
        return "没有可检查的内容。"

    if reason.startswith("could not read repository"):
        return f"读取失败：{reason.split(':', 1)[-1].strip()}"
    if "no reviewable Python files" in reason or "no Python files" in reason:
        detail = reason.split("(")[-1].rstrip(")") if "(" in reason else ""
        lines = ["这个文件夹里没有找到可以检查的 Python 代码。"]
        if detail:
            lines.append(f"\n跳过原因：{_translate_skip_detail(detail)}")
        return "\n".join(lines)
    return reason


def _translate_skip_detail(detail: str) -> str:
    """`2 not_python, 1 binary` -> `2 个非 Python 文件，1 个二进制文件`。"""
    parts = []
    for chunk in detail.split(","):
        bits = chunk.strip().split()
        if len(bits) == 2:
            count, why = bits
            parts.append(f"{count} 个{labels.skip_reason(why)}")
        else:
            parts.append(chunk.strip())
    return "，".join(parts)


def _render_header(report: ReviewReport, console: Console) -> None:
    console.print()
    console.print(f"[bold]代码审查[/bold]  [dim]{report.repo_root}[/dim]")
    if report.summary:
        console.print()
        console.print(Panel(report.summary, border_style="blue", title="摘要"))
    console.print()


def _render_finding(index: int, finding: VerifiedFinding, console: Console) -> None:
    colour = SEVERITY_COLOURS.get(finding.severity, "white")
    evidence = finding.evidence

    location = "(未标注位置)"
    if evidence:
        location = f"{evidence.path}:{evidence.line_start}"
        if evidence.line_end != evidence.line_start:
            location = f"{evidence.path}:{evidence.line_start}-{evidence.line_end}"

    heading = Text()
    heading.append(f"{index}. ", style="bold")
    heading.append(f"[{labels.severity(finding.severity)}]", style=f"bold {colour}")
    heading.append(f" {finding.title}", style="bold")
    console.print(heading)

    meta = f"   {location}   [dim]{labels.category(finding.category)}"
    if finding.source != "llm":
        meta += f" · {labels.source(finding.source)}"
    if finding.verdict == "uncertain":
        meta += " · 未获复核确认"
    console.print(f"{meta}[/dim]")

    console.print(f"   {finding.rationale}")

    if evidence and evidence.snippet:
        suffix = evidence.path.rsplit(".", 1)[-1] if "." in evidence.path else "text"
        console.print(
            Syntax(
                evidence.snippet,
                suffix,
                line_numbers=False,
                word_wrap=True,
                padding=(0, 3),
                theme="ansi_dark",
            )
        )

    if finding.suggested_fix:
        console.print(f"   [green]修改建议：[/green] {finding.suggested_fix}")

    console.print(f"[dim]{_RULE}[/dim]")


def _render_errors(report: ReviewReport, console: Console) -> None:
    if not report.errors:
        return

    stats = report.stats
    console.print()

    # 所有文件都失败了，说明 AI 那一层什么也没做。此时下面的结论只来自
    # 确定性检查，但报告并没有被标记成"部分检查"——这个区别很重要，否则
    # 读者会以为 AI 看过代码、只发现了这些。
    everything_failed = (
        not report.offline
        and stats.files_reviewed > 0
        and stats.llm_calls == 0
        and len(report.errors) >= stats.files_reviewed
    )

    if everything_failed:
        console.print(
            Panel(
                "所有文件都没能交给 AI 检查，因此下面的结论只来自\n"
                "确定性检查，AI 并没有阅读这份代码。\n\n"
                f"[dim]第一条错误：{report.errors[0]}[/dim]",
                border_style="red",
                title="[red]AI 检查未能执行[/red]",
            )
        )
        return

    console.print(f"[yellow]有 {len(report.errors)} 个文件没能检查：[/yellow]")
    for error in report.errors[:10]:
        console.print(f"  [dim]· {error}[/dim]")
    if len(report.errors) > 10:
        console.print(f"  [dim]… 另有 {len(report.errors) - 10} 个[/dim]")


def _render_footer(report: ReviewReport, console: Console) -> None:
    """运行统计——证明边界情况确实被处理了。"""
    stats = report.stats

    table = Table(title="本次运行统计", show_header=False, box=None, pad_edge=False)
    table.add_column(style="dim", justify="right")
    table.add_column()

    findings = report.reportable
    by_severity: dict[str, int] = {}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1

    severity_text = "、".join(
        f"{labels.severity(name)} {count} 条"
        for name, count in sorted(
            by_severity.items(), key=lambda item: SEVERITY_ORDER.get(item[0], 9)
        )
    ) or "无"

    table.add_row("文件", f"已检查 {stats.files_reviewed} 个，跳过 {stats.files_skipped} 个")
    table.add_row("发现问题", f"{len(findings)} 条（{severity_text}）")
    table.add_row("AI 提出的候选", str(stats.candidates))
    table.add_row(
        "已否决",
        f"{stats.total_rejected} 条"
        f"（证据对不上 {stats.rejected_by_evidence} 条，"
        f"复核不通过 {stats.rejected_by_verifier} 条）",
    )
    table.add_row("AI 调用次数", str(stats.llm_calls))
    table.add_row("工具调用次数", str(stats.tool_calls))

    if stats.files_failed:
        table.add_row("[yellow]失败的文件[/yellow]", str(stats.files_failed))
    if stats.malformed_responses:
        table.add_row("[yellow]格式错误的回复[/yellow]", str(stats.malformed_responses))

    if stats.prompt_cache_hit_tokens or stats.prompt_cache_miss_tokens:
        total_in = stats.prompt_cache_hit_tokens + stats.prompt_cache_miss_tokens
        pct = (stats.prompt_cache_hit_tokens / total_in * 100) if total_in else 0
        table.add_row(
            "用掉的字数(令牌)",
            f"输入 {total_in:,}（缓存命中 {pct:.0f}%），输出 {stats.output_tokens:,}",
        )

    table.add_row("耗时", f"{stats.duration_s:.1f} 秒")

    console.print()
    console.print(table)

    if stats.rejected_by_evidence:
        console.print(
            f"[dim]  有 {stats.rejected_by_evidence} 条结论引用的代码在文件里根本不存在，"
            f"已被丢弃。[/dim]"
        )
    console.print()
