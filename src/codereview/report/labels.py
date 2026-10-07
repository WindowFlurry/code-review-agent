"""报告用中文标签。

程序内部一律使用英文标识（severity / category 的取值），因为它们同时是
Pydantic 的校验值和去重逻辑的匹配键——改中文会牵动 schema、去重和数据交换。
语言差异只在这最后一层渲染时抹平：*给人看的*文字是中文，*给程序用的*标识
保持英文。

Keeping the internal values English and translating only at render time is what
lets the JSON report stay machine-readable while the console output is fully
Chinese.
"""

from __future__ import annotations

#: 严重程度
SEVERITY_LABEL: dict[str, str] = {
    "critical": "严重",
    "high": "高",
    "medium": "中",
    "low": "低",
    "info": "提示",
}

#: 问题类别
CATEGORY_LABEL: dict[str, str] = {
    "bug": "缺陷",
    "security": "安全",
    "performance": "性能",
    "style": "风格",
    "maintainability": "可维护性",
    "test-gap": "测试缺失",
}

#: 复核结论
VERDICT_LABEL: dict[str, str] = {
    "confirmed": "已确认",
    "uncertain": "未能确认",
    "rejected": "已否决",
    "pending": "待复核",
}

#: 来源
SOURCE_LABEL: dict[str, str] = {
    "llm": "AI 阅读",
    "ruff": "ruff 静态检查",
    "ast": "语法结构分析",
}

#: 跳过原因
SKIP_LABEL: dict[str, str] = {
    "binary": "二进制文件",
    "too_large": "文件过大",
    "not_python": "非 Python 文件",
    "unreadable": "无法读取",
    "empty": "空文件",
    "vendored": "第三方代码",
    "generated": "自动生成",
}


def severity(level: str) -> str:
    return SEVERITY_LABEL.get(level, level)


def category(name: str) -> str:
    return CATEGORY_LABEL.get(name, name)


def verdict(name: str) -> str:
    return VERDICT_LABEL.get(name, name)


def source(name: str) -> str:
    return SOURCE_LABEL.get(name, name)


def skip_reason(name: str | None) -> str:
    if not name:
        return "未知原因"
    return SKIP_LABEL.get(name, name)
