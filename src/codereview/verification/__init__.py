"""Verification: deterministic evidence binding, dedup, and rule cross-checks."""

from .dedup import deduplicate
from .evidence import bind_evidence, bind_many, reject

__all__ = ["bind_evidence", "bind_many", "deduplicate", "reject"]
