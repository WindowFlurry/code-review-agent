"""Measure precision and recall against the bundled fixtures.

Usage::

    python scripts/evaluate.py --offline      # deterministic layer only, no API key
    python scripts/evaluate.py                # full graph
    python scripts/evaluate.py --mode single  # baseline, for comparison

Ground truth is the ten defects catalogued in
``tests/fixtures/buggy_repo/MANIFEST.md``. A reported finding counts as a hit if
it lands on the right file within a few lines of a seeded defect; the tolerance
exists because a reviewer may reasonably cite a function's opening line rather
than the exact statement.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codereview.agent.graph import build_graph  # noqa: E402
from codereview.agent.state import empty_state  # noqa: E402
from codereview.config import get_settings  # noqa: E402
from codereview.models.findings import VerifiedFinding  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

#: (path, line, label) — verified against the fixture and ruff's output.
GROUND_TRUTH: list[tuple[str, int, str]] = [
    ("security_issues.py", 17, "eval() on untrusted input"),
    ("security_issues.py", 22, "MD5 for password hashing"),
    ("security_issues.py", 27, "assert as an authorization check"),
    ("security_issues.py", 8, "unused import"),
    ("security_issues.py", 12, "hardcoded credential"),
    ("quality_issues.py", 9, "mutable default argument"),
    ("quality_issues.py", 20, "bare except"),
    ("quality_issues.py", 26, "division without a zero check"),
    ("quality_issues.py", 31, "average() inherits the division bug"),
    ("quality_issues.py", 38, "except Exception: pass swallows KeyError"),
]

#: How far a finding's line may be from the seeded defect and still count.
LINE_TOLERANCE = 3


@dataclass
class Score:
    matched: list[str]
    missed: list[str]
    false_positives: list[str]

    @property
    def recall(self) -> float:
        total = len(self.matched) + len(self.missed)
        return len(self.matched) / total if total else 0.0

    @property
    def precision(self) -> float:
        reported = len(self.matched) + len(self.false_positives)
        return len(self.matched) / reported if reported else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def score(findings: list[VerifiedFinding]) -> Score:
    """Compare reported findings against the seeded ground truth."""
    hits = [f for f in findings if f.evidence]
    remaining = list(GROUND_TRUTH)
    matched: list[str] = []
    false_positives: list[str] = []

    for finding in hits:
        path = finding.evidence.path.replace("\\", "/").split("/")[-1]
        line = finding.evidence.line_start

        hit = next(
            (
                (gpath, gline, label)
                for gpath, gline, label in remaining
                if gpath == path and abs(gline - line) <= LINE_TOLERANCE
            ),
            None,
        )
        if hit is None:
            false_positives.append(f"{path}:{line} {finding.title[:50]}")
        else:
            remaining.remove(hit)
            matched.append(hit[2])

    return Score(matched, [label for _, _, label in remaining], false_positives)


def run_offline() -> tuple[list[VerifiedFinding], list[VerifiedFinding]]:
    """Deterministic layer only: ruff + AST, no model calls."""
    from codereview.tools.ast_tools import python_ast_summary
    from codereview.tools.ast_tools import to_candidates as ast_candidates
    from codereview.tools.lint_tools import run_lint
    from codereview.tools.lint_tools import to_candidates as lint_candidates
    from codereview.verification.dedup import deduplicate
    from codereview.verification.evidence import bind_many

    buggy = FIXTURES / "buggy_repo"
    clean = FIXTURES / "clean_repo"

    def collect(root: Path) -> list[VerifiedFinding]:
        candidates = lint_candidates(run_lint(root))
        for meta in sorted(root.glob("*.py")):
            candidates.extend(ast_candidates(python_ast_summary(root, meta.name)))
        bound = [f for f in bind_many(root, candidates) if f.is_reportable]
        # Dedup belongs here too: the graph merges cross-source duplicates in
        # `aggregate`. Measuring without it would count ruff's E722 and the AST
        # tool's bare-except finding as two separate results instead of one.
        return deduplicate(bound)

    return collect(buggy), collect(clean)


def run_graph_review(root: Path, mode: str) -> list[VerifiedFinding]:
    if mode == "single":
        from codereview.agent.simple_agent import run_simple_review

        state = run_simple_review(root, run_id="eval")
    else:
        state = build_graph().invoke(
            empty_state(str(root), "eval"), {"configurable": {"thread_id": "eval"}}
        )
    return list(state["report"].reportable)


def report(title: str, buggy_findings, clean_findings) -> None:
    s = score(buggy_findings)
    print(f"\n{'=' * 66}")
    print(f"  {title}")
    print(f"{'=' * 66}")
    print(f"  seeded defects      {len(GROUND_TRUTH)}")
    print(f"  findings reported   {len(buggy_findings)}")
    print(f"  matched             {len(s.matched)}")
    print(f"  precision           {s.precision:.0%}")
    print(f"  recall              {s.recall:.0%}")
    print(f"  f1                  {s.f1:.0%}")

    if s.matched:
        print("\n  caught:")
        for label in s.matched:
            print(f"    + {label}")
    if s.missed:
        print("\n  missed (recall):")
        for label in s.missed:
            print(f"    - {label}")
    if s.false_positives:
        print("\n  false positives (precision):")
        for label in s.false_positives:
            print(f"    ! {label}")

    print(f"\n  clean_repo findings {len(clean_findings)} (must be 0)")
    if clean_findings:
        print("    !! the agent reported defects in clean code:")
        for finding in clean_findings:
            location = finding.evidence.path if finding.evidence else "?"
            print(f"       {location}: {finding.title[:60]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="deterministic layer only")
    parser.add_argument("--mode", default="graph", choices=["graph", "single"])
    args = parser.parse_args()

    if args.offline:
        buggy, clean = run_offline()
        report("Deterministic layer only (ruff + AST, no LLM)", buggy, clean)
        return

    settings = get_settings()
    if not settings.has_api_key:
        print("No DEEPSEEK_API_KEY configured. Use --offline, or set the key in .env.")
        raise SystemExit(2)

    buggy = run_graph_review(FIXTURES / "buggy_repo", args.mode)
    clean = run_graph_review(FIXTURES / "clean_repo", args.mode)
    report(f"Full pipeline (mode={args.mode})", buggy, clean)


if __name__ == "__main__":
    main()
