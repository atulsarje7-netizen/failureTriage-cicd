"""Allow-listed same-SHA and last-green verification for failed tests."""

from triage.verify.verify import apply_verification, verify_report

__all__ = ["apply_verification", "verify_report"]
