"""Exception hierarchy.

Every failure mode the agent can hit has a named exception, so callers (and the
CLI) can distinguish "the user typed a bad path" from "the LLM API is down".
"""

from __future__ import annotations


class CodeReviewError(Exception):
    """Base class for everything this package raises deliberately."""


class ConfigError(CodeReviewError):
    """Missing or invalid configuration (bad API key, unreadable .env, ...)."""


class SandboxViolation(CodeReviewError):
    """A tool was asked to touch a path outside the repository root.

    Raised rather than silently returning empty, so the violation is visible in
    the run log instead of looking like "the file had no issues".
    """


class ToolError(CodeReviewError):
    """A tool failed for an operational reason (subprocess died, timeout, ...).

    Tool bodies catch these and hand the message back to the agent as text, so
    the model can adapt instead of the whole run aborting.
    """


class IngestionError(CodeReviewError):
    """The repository could not be walked or classified."""


class EvidenceMismatch(CodeReviewError):
    """A finding cited code that does not match what is actually on disk.

    This is the anti-hallucination gate firing. It is an expected, counted
    outcome -- not a crash.
    """


class LLMError(CodeReviewError):
    """The language model call failed after retries were exhausted."""
