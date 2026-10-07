"""Typed, validated configuration.

Settings are validated once at startup rather than failing deep inside an agent
run with a confusing API error. pydantic-settings maps ``DEEPSEEK_API_KEY`` in
the environment or ``.env`` onto :attr:`Settings.deepseek_api_key`
case-insensitively.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Fallback when no .env is found by walking up from the CWD.
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _find_dotenv() -> Path | None:
    """Look for .env beside the CWD, then in every parent, then the package root.

    Walking up matters because the tool is normally pointed at *some other*
    repository: ``codereview review ../other-project`` is run from wherever the
    user happens to be.
    """
    for base in (Path.cwd(), *Path.cwd().parents):
        candidate = base / ".env"
        if candidate.is_file():
            return candidate
    packaged = _PACKAGE_ROOT / ".env"
    return packaged if packaged.is_file() else None


class Settings(BaseSettings):
    """Runtime configuration for a review run."""

    model_config = SettingsConfigDict(
        env_file=_find_dotenv(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- credentials -------------------------------------------------------
    deepseek_api_key: SecretStr = Field(default=SecretStr(""))
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-flash"

    # ---- optional fallback provider ---------------------------------------
    dashscope_api_key: SecretStr = Field(default=SecretStr(""))
    fallback_model: str = ""
    fallback_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # ---- model behaviour ---------------------------------------------------
    # DeepSeek thinking mode is ON by default and silently ignores temperature.
    # Deterministic nodes turn it off; the reviewer turns reasoning effort up.
    disable_thinking: bool = True
    reasoning_effort: str = "high"

    # ---- review limits -----------------------------------------------------
    max_files: int = 40
    max_file_bytes: int = 262_144
    max_concurrency: int = 4
    max_tool_calls_per_file: int = 25
    llm_timeout_s: float = 120.0
    max_retries: int = 3

    # ---- paths -------------------------------------------------------------
    run_dir: Path = Path("runs")

    @field_validator("reasoning_effort")
    @classmethod
    def _check_effort(cls, v: str) -> str:
        allowed = {"low", "high", "max"}
        if v not in allowed:
            raise ValueError(f"reasoning_effort must be one of {sorted(allowed)}, got {v!r}")
        return v

    @field_validator("max_concurrency", "max_files", "max_file_bytes")
    @classmethod
    def _check_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be >= 1")
        return v

    @property
    def has_api_key(self) -> bool:
        return bool(self.deepseek_api_key.get_secret_value().strip())

    @property
    def has_fallback(self) -> bool:
        return bool(self.fallback_model.strip()) and bool(
            self.dashscope_api_key.get_secret_value().strip()
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (cached so the .env scan runs once)."""
    return Settings()
