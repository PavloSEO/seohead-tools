"""Phrase normalization, intent filters and stop lists shared by every stage.

The deduplication key is ``normalize(phrase)``. Intent patterns match on word boundaries so a
short stem does not fire inside an unrelated word. A project picks a preset in
``project.yaml`` and may override its ``intent``, ``stop`` and ``info`` patterns.
"""

from __future__ import annotations

import re


def normalize(s):
    s = (s or "").strip().lower().replace("ё", "е")
    s = re.sub(r"[^0-9a-zа-я \-]", " ", s)  # keep letters, digits, spaces and hyphens
    s = s.replace("-", " ")  # hyphen -> space: one key, and Arsenkin rejects some hyphens
    return re.sub(r"\s+", " ", s).strip()


# Generic junk shared by every topic, unless a project's own intent says otherwise.
JUNK = r"порно|секс|торрент|скачать бесплатн|взлом|кряк|бесплатно скачать"

# Example topic preset: SEO services and tools. Topic filters are per project, never global:
# a word that is junk for one topic is the intent of another.
SEO_INTENT = (
    r"\bseo\b|\bсео\b|продвижени\w*|оптимизаци\w*|аудит\w*|краулер\w*|парсер\w*|"
    r"индексаци\w*|\bsitemap\w*|\brobots\b|редирект\w*|\bканоническ\w*|\bcanonical\b|"
    r"битых? ссыл\w*|скорост\w* (сайт|загруз)|позици\w* сайт|семантическ\w* ядр\w*|"
    r"ключев\w* слов\w*|screaming frog|\bmcp\b"
)
SEO_INFO = r"что такое|как сделать|как провести|как проверить|зачем|пример|чек[- ]?лист|гайд"

PRESETS = {
    "seo": (SEO_INTENT, JUNK + r"|ваканси|курсы|обучени", SEO_INFO),
    # generic: no topic-intent filter, only universal junk.
    "generic": (None, JUNK + r"|ваканси|работа", r"(?!x)x"),
}


class Filters:
    """Compiled project filters: the preset is the base, explicit patterns override it."""

    def __init__(self, intent=None, stop=None, info=None, any_intent=False, preset="generic"):
        if preset not in PRESETS:
            raise ValueError(f"unknown preset: {preset}; choose one of {sorted(PRESETS)}")
        p_intent, p_stop, p_info = PRESETS[preset]
        eff_intent = intent or p_intent
        self.any = any_intent or (preset == "generic" and not intent)
        self.intent = None if self.any else re.compile(eff_intent) if eff_intent else None
        self.stop = re.compile(f"(?:{p_stop})|(?:{stop})" if stop else p_stop)
        self.info = re.compile(info or p_info)

    @classmethod
    def from_config(cls, cfg):
        return cls(
            cfg.get("intent") or None,
            cfg.get("stop") or None,
            cfg.get("info") or None,
            cfg.get("any_intent"),
            preset=cfg.get("preset", "generic"),
        )

    def is_stop(self, p):
        return bool(self.stop.search(p))

    def is_info(self, p):
        return bool(self.info.search(p))

    def has_intent(self, p):
        return True if self.any else bool(self.intent.search(p))

    def route(self, p):
        return "info" if self.is_info(p) else "commercial"

    def classify(self, p, base, min_base):
        """Fast clean-stage verdict: kept | review | dropped, plus the route."""
        if self.is_stop(p):
            return "dropped", self.route(p)
        if len(p) < 4 or len(p.split()) > 9:
            return "dropped", self.route(p)
        if not self.has_intent(p):
            return "review", self.route(p)  # no topic anchor: manual triage, not a drop
        if base is not None and base < min_base:
            return "review", self.route(p)  # low demand is doubtful, not junk yet
        return "kept", self.route(p)
