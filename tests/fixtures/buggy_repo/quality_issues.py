"""Deliberately sloppy code.

Defects catalogued in ``MANIFEST.md``. Roughly half are caught by ruff and half
are only visible on reading, which is what makes this fixture useful: it shows
where the deterministic tools stop and the model has to earn its keep.
"""


def add_item(item, items=[]):
    """BUG 6: mutable default argument shared across every call."""
    items.append(item)
    return items


def read_config(path):
    """BUG 7: bare `except:` hides every failure, including Ctrl-C."""
    try:
        with open(path) as handle:
            return handle.read()
    except:
        return None


def divide(numerator, denominator):
    """BUG 8: no zero check -- raises ZeroDivisionError on valid-looking input."""
    return numerator / denominator


def average(values):
    """BUG 9: same defect one level down, via divide()."""
    return divide(sum(values), len(values))


def name_of(record):
    """BUG 10: silently returns None for a missing key instead of raising."""
    try:
        return record["name"]
    except Exception:
        pass
