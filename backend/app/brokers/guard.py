"""PAPER-ONLY guard.

Every order-mutating call (preview / place / cancel / reply) must pass through
`require_paper_account`. IBKR paper account ids start with ``DU`` (individual
paper) or ``DF`` (paper financial advisor); live ids look like ``U1234567``.

There is no code path in this repo that disables the guard: `settings.ibkr_paper_only`
defaults to True and the .env.example ships it as true.
"""
from __future__ import annotations

from app.config import settings

PAPER_PREFIXES = ("DU", "DF")


class PaperOnlyViolation(Exception):
    """Raised when an order-mutating call targets a non-paper account."""

    def __init__(self, account_id: str | None, message: str | None = None) -> None:
        self.account_id = account_id or ""
        super().__init__(message or (
            f"Refusing to send an order for account '{self.account_id or '(none)'}': "
            "Vector Alpha is paper-trading only. Select an IBKR paper account "
            "(id starts with DU or DF) in the gateway and try again."
        ))


def is_paper_account(account_id: str | None) -> bool:
    """True when the account id looks like an IBKR paper account."""
    if not account_id:
        return False
    return account_id.strip().upper().startswith(PAPER_PREFIXES)


def require_paper_account(account_id: str | None) -> str:
    """Return the normalized account id, or raise PaperOnlyViolation.

    Also raises when no account is selected at all — we never guess.
    """
    acct = (account_id or "").strip()
    if not acct:
        raise PaperOnlyViolation(acct, "No IBKR account selected. Choose a paper account (DU…/DF…) first.")
    if not settings.ibkr_paper_only:
        # Escape hatch that this repo never enables; kept explicit so the intent is auditable.
        return acct
    if not is_paper_account(acct):
        raise PaperOnlyViolation(acct)
    return acct
