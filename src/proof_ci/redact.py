"""Output redaction used by the runner before anything is stored (spec Section 5.15).

The patterns live in :mod:`proof_ci._redact_patterns` so the pytest plugin can
share them without importing proof-ci's heavier dependencies.
"""

from __future__ import annotations

from proof_ci._redact_patterns import MAX_CAPTURE_CHARS, REPLACEMENT, redact, truncate_and_redact

__all__ = ["MAX_CAPTURE_CHARS", "REPLACEMENT", "redact", "truncate_and_redact"]
