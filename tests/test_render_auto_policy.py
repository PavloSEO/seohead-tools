"""``rendering.escalation.policy: "auto"`` -- the desktop Auto mode (#950).

Auto is sampled probing plus one extra verdict: a pattern whose sampled raw
page carries little text is escalated even when raw and rendered agree.
"""

from __future__ import annotations

import pytest

from seohead.checks import render as render_tool
from seohead.crawl import settings as crawl_config


@pytest.mark.parametrize(
    ("words", "thin"),
    [(0, True), (render_tool.EMPTY_BODY_WORDS - 1, True), (render_tool.EMPTY_BODY_WORDS, False)],
)
def test_little_text_uses_the_empty_body_threshold(words, thin):
    assert render_tool.little_text({"words": words}) is thin


def test_little_text_treats_a_missing_count_as_thin():
    assert render_tool.little_text({}) is True


def test_auto_is_an_accepted_escalation_policy():
    resolved = crawl_config.load(overrides={"rendering.escalation.policy": "auto"})
    assert resolved["rendering"]["escalation"]["policy"] == "auto"


def test_sampled_stays_the_default_policy():
    resolved = crawl_config.load(overrides={})
    assert resolved["rendering"]["escalation"]["policy"] == "sampled"
