"""Secret-shaped redaction rules (spec Section 5.15 item 4). Standard library only.

Both :mod:`proof_ci.redact` and the pytest plugin import this module, so it
must not import anything outside the standard library. Redaction is
best-effort, not a guarantee.
"""

from __future__ import annotations

import re
from collections.abc import Callable

__all__ = ["MAX_CAPTURE_CHARS", "PATTERNS", "REPLACEMENT", "redact", "truncate_and_redact"]

REPLACEMENT = "***"
MAX_CAPTURE_CHARS = 8192

# (pattern, keep_group): when keep_group is set, that group's text is kept and
# only the rest of the match is replaced, so "token=abc..." becomes "token=***".
PATTERNS: tuple[tuple[re.Pattern[str], int | None], ...] = (
    (
        re.compile(
            r"(?i)((?:api[_-]?key|secret|password|token|authorization|bearer)[\"'=\s:]+)[^\s\"'\\]{8,}"
        ),
        1,
    ),
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
        None,
    ),
    (re.compile(r"AKIA[0-9A-Z]{16}"), None),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), None),
    (re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"), None),
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), None),
)


def _keep_group(group: int) -> Callable[[re.Match[str]], str]:
    def replace(match: re.Match[str]) -> str:
        return match.group(group) + REPLACEMENT

    return replace


def redact(text: str) -> str:
    """Replace secret-shaped spans in ``text`` with ``***``."""
    for pattern, keep in PATTERNS:
        if keep is None:
            text = pattern.sub(REPLACEMENT, text)
        else:
            text = pattern.sub(_keep_group(keep), text)
    return text


def truncate_and_redact(text: str, limit: int = MAX_CAPTURE_CHARS) -> str:
    """Truncate to ``limit`` characters, then redact (spec Section 5.10 item 3)."""
    return redact(text[:limit])
