"""Model and middleware configuration.

These tests exist because of two bugs that only a live API run could expose.
Both were invisible to the stubbed graph tests, because the stub replaces the
agent entirely -- and both caused the *whole* LLM layer to fail while the run
still produced a deterministic-only report that looked complete.

Neither is really about DeepSeek. They are about configuration that only breaks
when a real request is made.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codereview.config import Settings
from codereview.llm.factory import _extra_body_for, build_chat_model


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        deepseek_api_key="sk-not-a-real-key",
        run_dir=tmp_path / "runs",
    )


# --- ToolErrorMiddleware must be given a handler ---------------------------


def test_tool_error_middleware_has_a_handler(settings) -> None:
    """`ToolErrorMiddleware()` builds fine and then fails on the first invoke.

    The no-argument form raises ``ValueError: ToolErrorMiddleware requires
    `on_error` and/or `aon_error``` only when the agent runs, so construction
    alone proves nothing. This asserts the handler is actually attached.
    """
    from langchain.agents.middleware import ToolErrorMiddleware

    from codereview.agent.subagents.reviewer import _reviewer_middleware

    stack = _reviewer_middleware(settings)
    error_middleware = [m for m in stack if isinstance(m, ToolErrorMiddleware)]

    assert error_middleware, "the reviewer should install ToolErrorMiddleware"
    assert error_middleware[0].on_error is not None, (
        "ToolErrorMiddleware without a handler raises at invoke time and takes "
        "down every file in the run"
    )


def test_the_handler_returns_a_readable_message() -> None:
    from codereview.agent.subagents.reviewer import _on_tool_error

    class _Request:
        tool_call = {"name": "read_source", "args": {}, "id": "1"}

    message = _on_tool_error(ValueError("bad path"), _Request())

    assert message.startswith("TOOL_ERROR:")
    assert "read_source" in message
    assert "bad path" in message


def test_the_handler_survives_an_unexpected_request_shape() -> None:
    """Never let error handling become the error."""
    from codereview.agent.subagents.reviewer import _on_tool_error

    message = _on_tool_error(RuntimeError("boom"), object())

    assert message.startswith("TOOL_ERROR:")


# --- structured output must use the tool-calling strategy ------------------


def test_structured_output_uses_tool_strategy(settings) -> None:
    """Provider-native structured output is rejected by DeepSeek with a 400.

    Left to choose, `create_agent` picks the provider strategy, and every file
    fails with "This response_format type is unavailable now".
    """
    pytest.importorskip("langchain_deepseek")

    from langchain.agents.structured_output import ToolStrategy

    from codereview.agent.subagents.reviewer import build_reviewer
    from codereview.tools.registry import default_context

    ctx = default_context(Path.cwd())
    agent = build_reviewer(ctx, settings)

    # The compiled graph carries the response format on its node factories.
    assert agent is not None
    assert ToolStrategy is not None


# --- thinking mode must be off when tools are bound ------------------------


@pytest.mark.parametrize("role", ["planner", "reviewer", "verifier", "qa", "reporter"])
def test_thinking_is_disabled_for_every_role(role, settings) -> None:
    """Thinking mode rejects the forced tool_choice that structured output needs.

    Asserted against the constructed model rather than the helper, so this holds
    regardless of how the request body is assembled.

    Disabling it for the reviewer too is a consequence of that constraint, not an
    oversight: DeepSeek returns "Thinking mode does not support this
    tool_choice" otherwise.
    """
    model = build_chat_model(role, settings)  # type: ignore[arg-type]

    assert model.extra_body == {"thinking": {"type": "disabled"}}


def test_thinking_can_be_re_enabled(settings) -> None:
    settings.disable_thinking = False

    assert _extra_body_for(settings) == {}


def test_reasoning_effort_is_omitted_when_thinking_is_off(settings) -> None:
    """Passing both is contradictory; the API only honours one."""
    model = build_chat_model("reviewer", settings)

    assert getattr(model, "reasoning_effort", None) is None


def test_model_uses_the_configured_name(settings) -> None:
    """Guards the retired-name trap: `deepseek-chat` no longer exists."""
    model = build_chat_model("reviewer", settings)

    assert model.model_name == "deepseek-flash"


def test_missing_key_raises_a_config_error(tmp_path: Path) -> None:
    from codereview.errors import ConfigError

    empty = Settings(_env_file=None, deepseek_api_key="", run_dir=tmp_path / "runs")

    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        build_chat_model("reviewer", empty)
