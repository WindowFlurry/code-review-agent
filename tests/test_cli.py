"""冒烟测试：命令行模块必须能导入。

这里曾经出过一次事故：``cli.py`` 里有一行字符串没闭合，而 112 项测试
没有任何一项 import 它，所以全绿；直到用户双击 bat 才报 SyntaxError。
一个 import 就能挡住这类错误。
"""

from __future__ import annotations


def test_cli_imports() -> None:
    from codereview.cli import app

    assert app is not None


def test_sample_command_is_registered() -> None:
    from codereview.cli import app

    # Typer leaves `name` as None when the command is named after the function.
    names = {
        command.name or getattr(command.callback, "__name__", "")
        for command in app.registered_commands
    }
    assert "sample" in names
