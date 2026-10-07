"""Deterministic linting via ruff.

Ruff findings are the strongest evidence in the whole pipeline: they come from a
real analyser, so they cannot be hallucinated and their line numbers are ground
truth. They also give the verifier something concrete to cross-check model claims
against -- if the reviewer says "unused import" but ruff reports no ``F401`` for
that file, that is a signal.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from ..errors import ToolError
from ..models.lint import LintFinding
from .sandbox import decode_text

logger = logging.getLogger(__name__)

_TIMEOUT_S = 60
_MAX_FINDINGS = 500

#: Rules worth surfacing as review findings on their own.
#: PTH (flake8-use-pathlib) is deliberately excluded: it fires on every
#: ``open()`` in a codebase and its complaints are stylistic preferences rather
#: than defects. Including it measurably hurt precision in evaluation.
DEFAULT_SELECT = "E,F,W,B,SIM,C4,RET,S"

#: Purely cosmetic rules, ignored even when their category is selected.
#:
#: A code review that leads with "line too long" is not a useful review, and on
#: a real project these drown everything else: a single file review produced 200
#: findings of which 166 were E501. They carry no information a maintainer would
#: act on, and they push the genuine defects off the first screen.
DEFAULT_IGNORE = "E501,W291,W293,W605,E731"

#: Broad categories mapped from ruff's rule-code prefixes.
_CATEGORY_BY_PREFIX: dict[str, str] = {
    "S": "security",
    "B": "bug",
    "E": "style",
    "W": "style",
    "F": "bug",
    "SIM": "maintainability",
    "C4": "performance",
    "RET": "maintainability",
    "PTH": "maintainability",
}

_SEVERITY_BY_PREFIX: dict[str, str] = {
    "S": "high",
    "B": "medium",
    "F": "medium",
    "E": "low",
    "W": "low",
}

#: Rules whose prefix gives the wrong answer.
#:
#: Two reasons a rule lands here. Some are genuinely bugs that happen to live in
#: a style-oriented category (``E722`` is a bare ``except:``, which silently
#: swallows ``KeyboardInterrupt`` -- that is not a style opinion). Others are
#: misclassified severity (``F401`` is an unused import, not a defect).
#:
#: The category also has to agree with the AST tool's, because dedup matches on
#: it: ruff reporting ``E722`` as "style" while the AST reports the same line as
#: a "bug" would defeat the merge and show the user the same defect twice.
_RULE_OVERRIDES: dict[str, tuple[str, str]] = {
    # E999 is the classic flake8 code for "file does not parse". ruff omits the
    # code for syntax errors, so the parser assigns E999 rather than leaving them
    # to fall through the prefix table -- which classified a SyntaxError as
    # "style / low", the single most severe thing a file can contain.
    "E999": ("bug", "critical"),
    "E722": ("bug", "medium"),  # bare except
    "E711": ("bug", "medium"),  # comparison to None with ==
    "E712": ("bug", "medium"),  # comparison to True/False with ==
    "F401": ("style", "low"),  # unused import
    "F811": ("bug", "medium"),  # redefinition shadows an earlier definition
    "F841": ("bug", "low"),  # assigned but never used
    "B006": ("bug", "high"),  # mutable default argument
    "B008": ("style", "low"),  # function call in a default
    "S110": ("bug", "medium"),  # try-except-pass
}


def ruff_available() -> bool:
    return shutil.which("ruff") is not None


def run_lint(
    root: Path | str,
    paths: list[str] | None = None,
    select: str = DEFAULT_SELECT,
    ignore: str = DEFAULT_IGNORE,
) -> list[LintFinding]:
    """Run ruff over *paths* (or the whole repo) and return structured findings.

    Args:
        root: Repository root; also the working directory for ruff.
        paths: Repo-relative files or directories. ``None`` lints everything.
            Callers reviewing a single file **must** pass it here, or ruff will
            analyse the whole parent directory -- which, for a file on the
            Desktop, means linting every project in sight.
        select: Comma-separated rule selection.
        ignore: Comma-separated rules to suppress regardless of *select*.

    Raises:
        ToolError: ruff is not installed, timed out, or failed to start.

    Note:
        ruff exits 1 when it *finds* problems, which is the normal case here, so
        only exit codes above 1 are treated as failures.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise ToolError(f"not a directory: {root}")

    if not ruff_available():
        raise ToolError("ruff is not installed or not on PATH")

    argv = [
        "ruff",
        "check",
        "--isolated",  # ignore the target repo's own pyproject/setup.cfg
        "--no-cache",
        "--exit-zero",
        "--output-format",
        "json",
        f"--select={select}",
    ]
    if ignore:
        argv.append(f"--ignore={ignore}")
    # `--` terminates option parsing so a path can never be read as a flag.
    argv += ["--", *(paths or ["."])]

    try:
        proc = subprocess.run(
            argv,
            cwd=root_path,
            capture_output=True,
            timeout=_TIMEOUT_S,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"ruff timed out after {_TIMEOUT_S}s") from exc
    except OSError as exc:
        raise ToolError(f"could not run ruff: {exc}") from exc

    if proc.returncode > 1:
        stderr = decode_text(proc.stderr)[0].strip()
        raise ToolError(f"ruff failed (exit {proc.returncode}): {stderr[:300]}")

    return _parse_ruff_json(proc.stdout, root_path)


def _parse_ruff_json(raw: bytes, root: Path) -> list[LintFinding]:
    """Map ruff's JSON array onto :class:`LintFinding`.

    Verified against the real schema: each entry has ``code``, ``message``,
    ``filename`` (absolute), ``location.row``, ``end_location.row``, ``url`` and
    an optional ``fix``.
    """
    text, _encoding = decode_text(raw)
    if not text.strip():
        return []

    try:
        records = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolError(f"could not parse ruff output as JSON: {exc}") from exc

    if not isinstance(records, list):
        raise ToolError("unexpected ruff output shape (expected a JSON array)")

    findings: list[LintFinding] = []

    for record in records[:_MAX_FINDINGS]:
        try:
            filename = str(record.get("filename", ""))
            path = _relative_to_root(filename, root)
            location = record.get("location") or {}
            end_location = record.get("end_location") or {}

            start = int(location.get("row", 1))
            end = int(end_location.get("row", start))

            message = str(record.get("message", ""))
            # ruff emits no rule code for a parse failure; without this it would
            # be filed as UNKNOWN and land in the "style / info" bucket.
            code = record.get("code") or (
                "E999" if "syntax" in message.lower() else "UNKNOWN"
            )

            findings.append(
                LintFinding(
                    rule_id=str(code),
                    message=str(record.get("message", "")),
                    path=path,
                    line_start=max(1, start),
                    line_end=max(1, end),
                    url=record.get("url"),
                    fixable=bool(record.get("fix")),
                )
            )
        except (TypeError, ValueError) as exc:
            logger.debug("skipping malformed ruff record: %s", exc)

    return findings


def _relative_to_root(filename: str, root: Path) -> str:
    """Convert ruff's absolute filename back to a repo-relative POSIX path."""
    try:
        return Path(filename).resolve().relative_to(root).as_posix()
    except ValueError:
        return Path(filename).as_posix()


def categorise(rule_id: str) -> tuple[str, str]:
    """Map a ruff rule code to ``(category, severity)``.

    Exact overrides win first; otherwise the longest matching prefix does, so
    ``SIM`` is matched before ``S``.
    """
    override = _RULE_OVERRIDES.get(rule_id)
    if override is not None:
        return override

    for prefix in sorted(_CATEGORY_BY_PREFIX, key=len, reverse=True):
        if rule_id.startswith(prefix):
            return (
                _CATEGORY_BY_PREFIX[prefix],
                _SEVERITY_BY_PREFIX.get(prefix, "low"),
            )
    return "style", "info"


#: 中文说明：``规则号 -> (一句话标题, 详细解释)``
#:
#: ruff 自带的消息是准确的，但它是写给已经知道 `E722` 是什么意思的人的。
#: 这份报告的读者是初学者，所以标题要能一眼看懂，解释要说明**为什么**是问题。
#: 表里没有的规则退回 ruff 原文——依然正确，只是更简短。
RULE_ZH: dict[str, tuple[str, str]] = {
    "E999": (
        "文件有语法错误，无法运行",
        "Python 解析不了这个文件，里面任何代码都跑不起来。"
        "通常是括号没配对、少了冒号，或者把别的语言的写法搬了过来。",
    ),
    "E722": (
        "裸露的 `except:` 会吞掉所有异常",
        "裸露的 `except:` 连 Ctrl-C 和程序退出信号都会一起捕获，"
        "真正出错时也看不到原因，程序还没法正常中断。"
        "应该写明你预期会出什么错，例如 `except ValueError:`。",
    ),
    "E711": (
        "判断 `None` 用了 `==`",
        "应该用 `is None`。`==` 可以被对象自定义，`is` 不会，判断 None 必须用 `is`。",
    ),
    "E712": (
        "和 `True`/`False` 做 `==` 比较",
        "直接写 `if x:` 或 `if not x:` 就行，不用和布尔值比较。",
    ),
    "E701": (
        "冒号后面直接跟了语句",
        "一行里塞了两条语句，读起来费劲，出错时行号也不好定位。建议换行。",
    ),
    "E702": (
        "用分号把多条语句挤在一行",
        "建议每条语句单独一行。Python 不像 C 语言，分号没有意义。",
    ),
    "E703": (
        "语句末尾多了个分号",
        "Python 不需要分号。这是从 C/Java 带过来的习惯。",
    ),
    "E731": (
        "用 `lambda` 给变量起名字",
        "应该用 `def` 定义函数。lambda 没有名字，报错信息里只会显示 `<lambda>`，"
        "出问题时很难定位。",
    ),
    "F401": (
        "import 进来了却没用到",
        "这个模块被导入了，但整个文件里一次都没用到，属于多余的代码。"
        "删掉它可以让依赖关系更清楚。",
    ),
    "F811": (
        "同一个名字被重复定义",
        "后面的定义覆盖了前面的，多半是复制粘贴忘了改。前面的那份代码实际上白写了。",
    ),
    "F841": (
        "变量赋值了却从没使用",
        "通常是漏写了后续代码，或者删代码时忘了删这一行。",
    ),
    "B006": (
        "参数默认值用了可变对象",
        "默认值只在函数定义时创建一次，所有调用共用同一个对象——"
        "上一次调用往里加的东西，下一次调用还在。"
        "正确写法是默认写 `None`，在函数体里再创建。",
    ),
    "B008": (
        "默认值里直接调用了函数",
        "默认值只在定义时求值一次，不会每次调用重新计算，行为和你想的多半不一样。",
    ),
    "S101": (
        "用 `assert` 做检查",
        "Python 加 `-O` 参数运行时，所有 assert 会被整个删掉。"
        "所以它只能用来做调试断言，不能用来做权限校验或参数校验。",
    ),
    "S105": (
        "代码里写死了密码或密钥",
        "密码写进源码后，会跟着代码一起进版本库、进聊天记录、进压缩包。"
        "应该放到环境变量或配置文件里，并且不要把配置文件提交上去。",
    ),
    "S106": ("函数参数默认值是写死的密码", "同 S105：凭据不应该出现在源码里。"),
    "S107": ("函数参数默认值是写死的密码", "同 S105：凭据不应该出现在源码里。"),
    "S110": (
        "`try/except` 里什么都没做",
        "异常被静默吞掉了。程序看着没报错，实际上什么都没干成，"
        "出问题时完全查不到原因。至少应该记一条日志。",
    ),
    "S307": (
        "用了 `eval()`，存在代码注入风险",
        "如果参数来自用户输入，等于允许别人在你的程序里执行任意代码。"
        "只是想把字符串转成列表/字典的话，用 `ast.literal_eval`。",
    ),
    "S324": (
        "用了已被破解的哈希算法",
        "MD5 和 SHA1 早就可以被人为碰撞，不能用来存密码。"
        "存密码请用 bcrypt 或 argon2，它们专门设计得慢。",
    ),
    "S501": (
        "关闭了 HTTPS 证书校验",
        "通信内容可能被中间人截获和篡改。",
    ),
    "S602": (
        "`shell=True` 执行命令，存在命令注入风险",
        "如果命令里拼接了用户输入，对方可以借机执行任意系统命令。"
        "改用参数列表形式，不要走 shell。",
    ),
}


def to_candidates(findings: list[LintFinding]):
    """Convert lint findings into tool-sourced candidates.

    These carry ``source="ruff"``, which lets evidence binding skip the
    snippet-matching step and mark them confirmed -- a real tool reported them.
    """
    from ..models.findings import CandidateFinding, Evidence

    candidates = []
    for finding in findings:
        category, severity = categorise(finding.rule_id)
        short, detail = RULE_ZH.get(finding.rule_id, (finding.message, ""))

        # Title is the short label; the full explanation goes in the rationale.
        # Putting the whole paragraph in the title made the finding header
        # unreadable -- it wrapped over four lines before the location appeared.
        title = f"{finding.rule_id}：{short}"
        rationale = (
            # ruff's own wording is kept alongside: the rule code is what the
            # documentation and a search engine are keyed on.
            f"{detail}\n\n（{finding.rule_id}：{finding.message}）"
            if detail
            else f"ruff 静态检查报告（{finding.rule_id}）：{finding.message}"
        )

        if finding.fixable:
            suggested_fix = f"运行 `ruff check --select {finding.rule_id} --fix <文件>` 可自动修复"
        elif finding.url:
            # The renderer already prefixes "修改建议：", so this is just the
            # destination -- prefixing again produced "修改建议：规则说明：https://".
            suggested_fix = f"规则说明见 {finding.url}"
        else:
            suggested_fix = None

        candidates.append(
            CandidateFinding(
                category=category,  # type: ignore[arg-type]
                severity=severity,  # type: ignore[arg-type]
                title=title,
                rationale=rationale,
                evidence=Evidence(
                    path=finding.path,
                    line_start=finding.line_start,
                    line_end=finding.line_end,
                ),
                suggested_fix=suggested_fix,
                confidence=0.95,
                source="ruff",
                rule_id=finding.rule_id,
            )
        )
    return candidates
