# buggy_repo — seeded defects

Ground truth for recall. Line numbers below were verified by running
`ruff check --isolated --select=E,F,W,B,SIM,C4,RET,S` on this directory.

| # | File | Line | Defect | Kind | Detected by |
|---|------|------|--------|------|-------------|
| 1 | `security_issues.py` | 17 | `eval()` on untrusted input — arbitrary code execution | security / critical | ruff `S307` + LLM |
| 2 | `security_issues.py` | 22 | MD5 used for password hashing | security / high | ruff `S324` + LLM |
| 3 | `security_issues.py` | 27 | `assert` guarding an authorization check (removed under `-O`) | security / high | ruff `S101` + LLM |
| 4 | `security_issues.py` | 8 | `json` imported but unused | style / low | ruff `F401` |
| 5 | `security_issues.py` | 12 | Hardcoded credential | security / critical | ruff `S105` + LLM |
| 6 | `quality_issues.py` | 9 | Mutable default argument `items=[]` | bug / high | ruff `B006` + AST |
| 7 | `quality_issues.py` | 20 | Bare `except:` | bug / medium | ruff `E722` + AST |
| 8 | `quality_issues.py` | 26 | Division with no zero check | bug / medium | LLM only |
| 9 | `quality_issues.py` | 31 | `average()` inherits bug 8 through `divide()` | bug / medium | LLM only |
| 10 | `quality_issues.py` | 38 | `except Exception: pass` swallows a `KeyError` | bug / medium | ruff `S110` + LLM |

Total: **10** seeded defects. Ruff alone finds 8; defects 8 and 9 are invisible
to it and need a reader.

## Interpreting the result

- **Recall** = seeded defects reported ÷ 10.
- **Precision** = findings matching a seeded defect ÷ all findings reported.

Two deliberate decoys must *not* be reported:

- `getenv_or_default()` in `security_issues.py` — correct code, adjacent to a
  credential defect, and an easy thing to over-flag.
- The docstrings — `run_user_expression` and friends have docstrings that say
  "BUG 1: …". Those markers are comments about the defect, not the defect.
  A reviewer that reports "the docstring admits a bug" as a separate finding is
  double-counting.

Bugs 8 and 9 are the reason the LLM reviewer is in the pipeline at all. A run
whose findings come only from ruff scores 8/10 recall and misses the two defects
that actually crash the program at runtime.
