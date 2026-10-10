"""Shared confirmation gate for paid provider calls.

A paid adapter calls :func:`confirmation_required` before it builds a client or sends a request.
Without ``confirm_paid=True`` the gate returns a refusal envelope and no network call happens.
Usage is not converted into money here; see :mod:`seohead.data_sources.spend`.
"""

from __future__ import annotations


def confirmation_required(
    confirm_paid: bool, operation: str, estimate: dict | None = None
) -> dict | None:
    """Return a refusal envelope unless the caller explicitly confirmed the paid call."""
    if confirm_paid is True:
        return None
    return {
        "ok": False,
        "state": "confirmation_required",
        "operation": operation,
        "error": f"{operation} is paid and requires confirm_paid=true",
        "estimate": estimate,
    }
