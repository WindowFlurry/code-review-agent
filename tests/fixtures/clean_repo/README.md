# clean_repo

Fixture repository containing only correct code.

Used by `tests/test_evidence.py` and the end-to-end anti-hallucination check:
a review of this directory **must** return zero confirmed findings. Any finding
here is a false positive, and the report's `rejected_by_evidence` counter shows
whether the gate caught it or whether it leaked through.
