"""Deliberately insecure code.

Every defect here is intentional and catalogued in ``MANIFEST.md``. Do not
"fix" this file -- it is the fixture that measures recall.
"""

import hashlib
import json  # BUG 4: unused import -- reported by ruff as F401
import os

# BUG 5: credential committed to source.
API_PASSWORD = "hunter2-do-not-ship"


def run_user_expression(expression: str):
    """BUG 1: arbitrary code execution via eval on untrusted input."""
    return eval(expression)


def hash_password(password: str) -> str:
    """BUG 2: MD5 is broken for password hashing; use bcrypt/argon2."""
    return hashlib.md5(password.encode()).hexdigest()


def delete_account(user, request) -> bool:
    """BUG 3: `assert` is stripped under `python -O`, so this check vanishes."""
    assert request.user.is_admin
    user.delete()
    return True


def getenv_or_default(name: str) -> str:
    """Clean, for contrast -- a reviewer should not flag this."""
    return os.environ.get(name, "")
