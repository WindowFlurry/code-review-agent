"""Shared fixtures.

The stub agent is the important part. It lets the whole graph -- evidence
binding, dedup, aggregation, reporting -- be exercised with no network, no API
key, and no cost, which is what makes the pipeline testable in CI. Only the
model's judgement is faked; everything downstream of it is the real code.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# Make `src/` importable without an editable install.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def clean_repo() -> Path:
    """A repository that must produce zero findings."""
    return FIXTURES / "clean_repo"


@pytest.fixture
def buggy_repo() -> Path:
    """A repository with ten catalogued defects."""
    return FIXTURES / "buggy_repo"


@pytest.fixture
def weird_repo() -> Path:
    """Binary, UTF-16, GBK, empty, oversized, and unparseable files."""
    return FIXTURES / "weird_repo"


@dataclass
class FakeAgentResult:
    """Mimics the dict a compiled LangGraph agent returns."""

    messages: list = field(default_factory=list)
    structured_response: object = None


class FakeAgent:
    """Stands in for a compiled reviewer/verifier agent.

    Returns queued structured responses in order, so a test can script exactly
    what the model "found" without touching a provider.
    """

    def __init__(self, responses: list | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[dict] = []

    def invoke(self, payload, **_kwargs):  # noqa: ANN001, ANN201
        """Accepts and ignores the `config=` keyword a real agent takes."""
        self.calls.append(payload)
        response = self._responses.pop(0) if self._responses else None
        return FakeAgentResult(messages=[], structured_response=response)


@pytest.fixture
def fake_agent_factory():
    """Build a FakeAgent seeded with a list of structured responses."""
    return FakeAgent
