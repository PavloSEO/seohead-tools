"""Observed-demand predicate shared by the semantic-core stages."""

from __future__ import annotations


def has_observed_demand(impr, pos_y, pos_g) -> bool:
    """True when a phrase has positive impressions or a positive Yandex or Google position."""
    return any(value is not None and value > 0 for value in (impr, pos_y, pos_g))
