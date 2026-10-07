# Evaluation

Measured against the two bundled fixtures. Everything below is reproducible:

```bash
python scripts/evaluate.py --offline      # deterministic layer, no API key
python scripts/evaluate.py                # full pipeline
python scripts/evaluate.py --mode single  # baseline, for comparison
```

## Method

| Fixture | What it is |
|---|---|
| `tests/fixtures/buggy_repo` | 10 seeded defects, catalogued in `MANIFEST.md` |
| `tests/fixtures/clean_repo` | Correct code — any finding here is a false positive |

- **Hit** — a reported finding on the right file within ±3 lines of a seeded
  defect. The tolerance is deliberate: citing a function's opening line rather
  than the exact statement is a reasonable review comment, not an error.
- **Precision** = hits ÷ findings reported. **Recall** = hits ÷ 10.
- `clean_repo` is scored separately and has no tolerance: the correct answer is
  exactly zero findings.

---

## Result 1 — the deterministic layer alone

Ruff plus the AST analyser, no model calls at all.

```
seeded defects      10
findings reported    8
matched              8
precision          100%
recall              80%
f1                  89%
```

Caught: mutable default argument, bare `except:`, `except Exception: pass`,
unused import, hardcoded credential, `eval()` on untrusted input, MD5 for
password hashing, `assert` as an authorization check.

Missed:
- **division without a zero check** (`quality_issues.py:26`)
- **`average()` inherits the division bug** (`quality_issues.py:31`)

### Why this is the interesting number

The two missed defects are not oversights — they are *undetectable* by static
analysis. `return numerator / denominator` is syntactically perfect. Nothing in
the AST distinguishes it from correct code; knowing it is a bug requires knowing
that a zero denominator is possible, which means reading the code and reasoning
about how it is called.

So this row is the justification for the whole LLM layer: **it fixes the floor at
80% and the remaining 20% is exactly the part that needs judgement.** It also
sets the bar for the full pipeline — anything below 80% recall means the model is
*subtracting* value.

Precision is 100%, which matters more than it looks: it means the deterministic
findings can be trusted as anchors. When the verifier cross-checks a model claim
against ruff's output, it is checking against something with no false positives
in this sample.

### The clean-repo result

```
clean_repo findings  0
```

Zero. This is the control that gives the buggy-repo numbers meaning: the layer is
not simply reporting everything it can find.

---

## Result 2 — full pipeline

```
seeded defects      10
findings reported   10
matched             10
precision          100%
recall             100%
f1                 100%

clean_repo findings  0
```

Every seeded defect found, nothing invented. The two defects the deterministic
layer cannot see — `divide()` with no zero check and `average()` inheriting it —
were both caught by the reviewer and survived verification.

Measured on the failing-repository run: 6 LLM calls, 7 tool calls, 16,169 input
tokens (56% served from cache), 2,571 output tokens, 10.9 s.

### Run-to-run variance

Recall is not perfectly stable. Repeat runs on the same fixture produced 10 and
9 findings — the model missed one defect on one occasion and caught it on the
next. DeepSeek's thinking mode (which would be the usual source of determinism
problems, since it ignores `temperature`) is disabled here, so the variance is
ordinary sampling.

This is worth stating plainly because it qualifies the headline numbers: **100%
recall is the result of one run, not a guarantee.** A fixture of ten defects in
two files is small enough that one miss moves recall by ten points. Treat the
figures as "this works reliably", not "this works every time".

### The number that matters is the clean-repo one

100% recall on a deliberately buggy fixture is easy to game — report more, find
more. `clean_repo findings 0` is the constraint that makes the recall figure
mean something: the same pipeline, on correct code, reported **nothing**. And
`rejected_by_evidence` was non-zero on runs where the model over-reached, which
is the gate doing its job rather than a claim that it exists.

---

## Result 3 — single-agent baseline

Same tools, same model, same deterministic prefetch and evidence binding. The
only difference is the architecture: no fan-out, no independent verifier.

```
seeded defects      10
findings reported   10
matched              8
precision           80%
recall              80%
f1                  80%

clean_repo findings  0
```

### Side by side

| | Layered pipeline | Single-agent baseline |
|---|---|---|
| Precision | **100%** | 80% |
| Recall | **100%** | 80% |
| `divide()` / `average()` caught | **yes** | no |
| Duplicate findings | 0 | 2 |

The baseline's two "false positives" are duplicates: it re-reported the mutable
default argument and the bare `except:` that ruff had already flagged, having
described them in its own words with a different category. The aggregated
pipeline removes these two ways — the reviewer is told which findings are already
covered, and `aggregate` merges what still overlaps.

### What this does and does not show

**Supported:** on this fixture, the layered pipeline reaches full recall where
the baseline stalls at 80%, and produces a cleaner report. The two missed defects
are precisely the ones needing reasoning about how a function gets called — the
class of bug a single pass over a whole repository is most likely to skim past.

**Not supported:** these are 10 defects in 2 small files. A 20-point recall gap
on a fixture this size is suggestive, not conclusive, and the baseline's cost
advantage (one conversation versus per-file fan-out) is real and would matter
more on a larger repository, where the gap in its favour would grow. The honest
summary is that the architecture earns its complexity on recall and report
quality here, and the case on cost is unresolved at this scale.

---

## What the automated tests already cover

Independent of the numbers above, `pytest` (112 tests, no network) pins the
behaviour that matters:

| Guarantee | Test |
|---|---|
| A fabricated finding never reaches the report | `test_hallucinated_finding_never_reaches_the_report` |
| The reported snippet comes from disk, not the model | `test_snippet_comes_from_disk_not_from_the_model` |
| Off-by-a-few line numbers still match | `test_tolerates_line_number_drift` |
| Clean code yields zero findings | `test_clean_code_produces_no_surviving_findings` |
| Parallel branches do not overwrite each other | `test_parallel_branches_do_not_clobber_each_other` |
| `../` and symlink escapes are refused | `test_sandbox.py` |
| UTF-16 and GBK files are read, binaries are skipped | `test_ingestion.py` |
| Binary / empty / oversized / unparseable files | `test_classifies_each_edge_case` |
| The verifier is skipped for tool-sourced findings | `test_tool_findings_are_included_without_the_model` |

These are guarantees, not measurements — they hold regardless of what the model
does on a given run, which is why they are the more durable form of evidence.
