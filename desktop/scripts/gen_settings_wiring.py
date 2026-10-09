#!/usr/bin/env python3
"""Regenerate desktop/docs/spec/settings-wiring.ru.md from ui/settings/wiring.py and the schema."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from seohead_desktop.ui.settings import full_schema, wiring  # noqa: E402

HEAD = """# Настройки: что уже работает, что заработает позже

Файл генерируется: `python scripts/gen_settings_wiring.py` (источник — `ui/settings/wiring.py`).
Тест `test_settings_wiring` падает, если ключ схемы не классифицирован или файл устарел.
Поле «Заработает позже» в окне «Настройки» помечено бейджем и не делает вид, что работает.

| Ключ | Сейчас | Заработает на шаге | Что нужно сначала |
|---|---|---|---|
"""


def render():
    lines = [HEAD.rstrip("\n")]
    for key, step, reason in wiring.table(full_schema()):
        lines.append(f"| `{key}` | {'работает' if step is None else 'только сохраняется'} | {step or '—'} | {reason or '—'} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    target = ROOT / "docs/spec/settings-wiring.ru.md"
    target.write_text(render(), encoding="utf-8")
    print(target)
