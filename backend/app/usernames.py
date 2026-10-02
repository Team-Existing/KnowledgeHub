"""
Usernames are unique regardless of case and Unicode form: "Alice", "alice"
and " ALICE " are the same name. The display form keeps the user's casing;
uniqueness and lookups use `username_key`, which has a UNIQUE index (created
by app/migrations.py once existing rows are backfilled).

New names are limited to ASCII letters, digits and . _ - so look-alike
characters (a Cyrillic "а" for a Latin "a") can't produce a second account
that reads the same as someone else's in member lists and invitations.
"""
from __future__ import annotations

import re
import unicodedata

USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,59}$"
USERNAME_RULES = "3 to 60 characters: letters, numbers, dot, underscore or hyphen, starting with a letter or number"
_PATTERN = re.compile(USERNAME_PATTERN)


def normalize(name: str) -> str:
    """The stored display form: Unicode-normalised, surrounding whitespace removed."""
    return unicodedata.normalize("NFKC", name).strip()


def username_key(name: str) -> str:
    """What uniqueness and sign-in compare: the normalised name, case-folded."""
    return normalize(name).casefold()


def check_new_username(name: str) -> str:
    """Validate a username for a new account; returns its display form. Raises ValueError."""
    display = normalize(name)
    if not _PATTERN.match(display):
        raise ValueError(f"Username must be {USERNAME_RULES}")
    return display
