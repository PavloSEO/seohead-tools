#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
exec .venv/bin/python -m PyInstaller --noconfirm --onedir --windowed --name "SEOHEAD Desktop" --osx-bundle-identifier tech.seohead.desktop --collect-data seohead_desktop scripts/entrypoint.py
