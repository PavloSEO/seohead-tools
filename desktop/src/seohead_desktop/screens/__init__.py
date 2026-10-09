"""Design-v2 screens. Each module registers its class in SCREENS; the window swaps them in for the legacy pages."""

from __future__ import annotations

from importlib import import_module

from .base import SLOTS

# slot -> "module:Class"; modules appear as screens are rebuilt from the canvas
SCREENS = {}


def install_screens(window):
    """Replace legacy pages by the screens registered in SCREENS (legacy widgets stay referenced, hidden)."""
    window._legacy_pages = getattr(window, "_legacy_pages", {})
    window.screens = getattr(window, "screens", {})
    for slot, target in SCREENS.items():
        module, _, name = target.partition(":")
        screen = getattr(import_module(f"{__name__}.{module}"), name)(window)
        index = SLOTS[slot]
        legacy = window.pages.widget(index)
        window.pages.removeWidget(legacy)
        legacy.hide()
        window._legacy_pages[slot] = legacy
        window.pages.insertWidget(index, screen)
        window.screens[slot] = screen
