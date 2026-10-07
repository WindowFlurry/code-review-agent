"""打包提交用：把项目压成一个 zip，自动排除不该交出去的东西。

用法::

    python scripts/pack.py 10086张三

压缩包会生成在项目文件夹的**上一层**（通常是桌面），文件名就是「学号姓名.zip」。

为什么用 Python 而不是 PowerShell 的 Compress-Archive：那条路要把中文文件名
在 cmd、PowerShell、控制台代码页之间来回传，任何一环编码对不上就会打包失败，
或者打出一个名字乱码的包。Python 在 Windows 上直接用宽字符 API 拿文件路径，
不存在这个问题。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

#: 不进压缩包的东西，以及原因。
EXCLUDE_NAMES = {
    ".venv": "运行环境（447MB，远超 200MB 限制）",
    ".env": "含 AI 密钥，绝对不能交出去",
    ".claude": "编辑器本地配置，与作业无关",
    "runs": "运行产生的报告，不需要提交",
    "__pycache__": "Python 缓存",
    ".pytest_cache": "测试缓存",
    ".ruff_cache": "lint 缓存",
}

#: 压缩包大小上限（作业要求 <200MB）。
SIZE_LIMIT_MB = 200


def collect(root: Path) -> list[Path]:
    """列出所有要打包的文件，跳过排除项。"""
    files: list[Path] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue

        relative = path.relative_to(root)
        if any(part in EXCLUDE_NAMES for part in relative.parts):
            continue
        if path.name.startswith("~$"):  # Office 打开文件时的临时锁文件
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue

        files.append(relative)

    return files


def _names(archive_path: Path) -> list[str]:
    with zipfile.ZipFile(archive_path) as archive:
        return archive.namelist()


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("用法：python scripts/pack.py 学号姓名")
        return 2

    name = sys.argv[1].strip()
    # 学号姓名里出现路径分隔符或冒号的话，会跑到别的目录去。
    for bad in ('\\', '/', ':', '*', '?', '"', '<', '>', '|'):
        if bad in name:
            print(f"名字里不能有 {bad!r} 这个字符，请重新输入。")
            return 2

    root = Path(__file__).resolve().parents[1]
    destination = root.parent / f"{name}.zip"

    files = collect(root)
    if not files:
        print("没有找到要打包的文件，是不是在错误的目录里运行的？")
        return 1

    print(f"正在打包 {len(files)} 个文件……")

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in files:
            archive.write(root / relative, arcname=relative.as_posix())

    size_mb = destination.stat().st_size / (1024 * 1024)

    # 打包完再验一遍：排除规则要是写漏了，宁可在这一步就发现，
    # 也不要等交上去才发现密钥跟着代码一起交了。
    #
    # 只匹配真正的 .env，不匹配 .env.example —— 后者是给人抄的模板，
    # 里面只有占位符，本来就应该随代码一起交。
    leaked = [
        n for n in _names(destination)
        if n in {".env", ".env.local"} or n.startswith(".venv/")
    ]

    print()
    print("=" * 60)
    print(f"  打包完成：{destination}")
    print(f"  大小：{size_mb:.1f} MB（上限 {SIZE_LIMIT_MB} MB）")
    print("=" * 60)

    if leaked:
        print()
        print("  ！！注意：压缩包里出现了不该有的文件：")
        for item in leaked:
            print(f"      {item}")
        print("  ！！请检查后再提交。")
        return 1

    if size_mb > SIZE_LIMIT_MB:
        print()
        print(f"  ！压缩包超过 {SIZE_LIMIT_MB} MB 了，需要再删点东西。")
        return 1

    print()
    print("  已确认：压缩包里没有 .env，也没有 .venv。")
    print("  提交前建议再自己双击打开看一眼。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
