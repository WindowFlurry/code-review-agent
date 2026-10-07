"""Small arithmetic helpers.

This module is deliberately, boringly correct. It is the fixture the
anti-hallucination gate is measured against: a reviewer that reports anything
here is wrong, and the report must say zero findings.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ratio:
    """A numerator and denominator kept as an exact pair."""

    numerator: float
    denominator: float

    def value(self) -> float:
        """Return the quotient.

        Raises:
            ValueError: if the denominator is zero.
        """
        if self.denominator == 0:
            raise ValueError("denominator must not be zero")
        return self.numerator / self.denominator


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""
    return left + right


def clamp(value: int, low: int, high: int) -> int:
    """Constrain *value* to the inclusive range ``[low, high]``.

    Raises:
        ValueError: if *low* exceeds *high*.
    """
    if low > high:
        raise ValueError(f"low ({low}) must not exceed high ({high})")
    return max(low, min(value, high))


def chunk(items: list[int], size: int) -> list[list[int]]:
    """Split *items* into consecutive groups of at most *size*.

    Args:
        items: The sequence to split.
        size: Maximum length of each group. Must be positive.

    Returns:
        A list of groups; empty if *items* is empty.

    Raises:
        ValueError: if *size* is not positive.
    """
    if size <= 0:
        raise ValueError(f"size must be positive, got {size}")
    return [items[index : index + size] for index in range(0, len(items), size)]
