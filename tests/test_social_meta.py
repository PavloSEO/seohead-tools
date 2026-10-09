"""Network-independent tests for the Open Graph and Twitter Card checklist."""

from seohead.checks import social_meta as SM

OG_FULL = {
    "og:title": "T",
    "og:type": "article",
    "og:url": "https://example.com/a",
    "og:image": "https://example.com/i.jpg",
    "og:image:alt": "alt",
    "og:description": "d",
    "og:site_name": "X",
    "og:locale": "ru_RU",
}
TW_FULL = {
    "twitter:card": "summary",
    "twitter:title": "T",
    "twitter:description": "d",
    "twitter:image": "https://example.com/i.jpg",
    "twitter:image:alt": "alt",
    "twitter:site": "@x",
    "twitter:creator": "@y",
}


def test_full_og_and_twitter_pass():
    r = SM.check_social_meta(og=OG_FULL, twitter=TW_FULL)
    assert r["og_complete"] is True
    assert r["twitter_complete"] is True
    assert not any("missing required" in f for f in r["findings"])


def test_missing_required_og_reported():
    og = dict(OG_FULL)
    del og["og:title"]
    r = SM.check_social_meta(og=og, twitter=TW_FULL)
    assert r["og_complete"] is False
    assert any(m["tag"] == "og:title" and m["level"] == "required" for m in r["og_missing"])


def test_image_alternatives_accepted():
    # og:image:src is an accepted alternative to og:image.
    og = {k: v for k, v in OG_FULL.items() if k != "og:image"}
    og["og:image:src"] = "https://example.com/i.jpg"
    r = SM.check_social_meta(og=og, twitter={})
    assert all(m["tag"] != "og:image" for m in r["og_missing"])


def test_missing_twitter_card():
    tw = dict(TW_FULL)
    del tw["twitter:card"]
    r = SM.check_social_meta(og=OG_FULL, twitter=tw)
    assert r["twitter_complete"] is False
    assert any(m["tag"] == "twitter:card" for m in r["twitter_missing"])


def test_no_tags_at_all_flagged():
    r = SM.check_social_meta(og={}, twitter={})
    assert any("No Open Graph or Twitter tags" in f for f in r["findings"])


def test_only_og_present_warns_twitter_absent():
    r = SM.check_social_meta(og=OG_FULL, twitter={})
    assert any("Twitter Card tags are absent" in f for f in r["findings"])
