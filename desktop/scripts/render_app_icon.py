#!/usr/bin/env python3
"""Render the original SEOHEAD SVG into native icon containers with Qt."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGuiApplication, QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "seohead_desktop" / "assets"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)


def render(source: Path, output: Path) -> list[Path]:
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError("invalid SEOHEAD source SVG")
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for size in SIZES:
        canvas = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        renderer.render(painter)
        painter.end()
        path = output / f"seohead-{size}.png"
        if not canvas.save(str(path), "PNG"):
            raise OSError(f"could not write icon: {path}")
        paths.append(path)

    # ICO supports PNG payloads; keep every size rather than upscaling one bitmap.
    frames = [
        (size, (output / f"seohead-{size}.png").read_bytes())
        for size in SIZES
        if size <= 256
    ]
    offset = 6 + 16 * len(frames)
    directory = []
    for size, data in frames:
        directory.append(
            struct.pack(
                "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset
            )
        )
        offset += len(data)
    ico = output / "seohead.ico"
    ico.write_bytes(
        struct.pack("<HHH", 0, 1, len(frames))
        + b"".join(directory)
        + b"".join(data for _, data in frames)
    )
    paths.append(ico)

    iconutil = shutil.which("iconutil")
    if iconutil:
        scratch = ROOT / ".build" / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="icons-", dir=scratch) as temporary:
            iconset = Path(temporary) / "SEOHEAD.iconset"
            iconset.mkdir()
            for size in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    suffix = "@2x" if scale == 2 else ""
                    shutil.copyfile(
                        output / f"seohead-{size * scale}.png",
                        iconset / f"icon_{size}x{size}{suffix}.png",
                    )
            icns = output / "seohead.icns"
            subprocess.run(
                [iconutil, "--convert", "icns", "--output", str(icns), str(iconset)],
                check=True,
            )
            paths.append(icns)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ASSETS / "app")
    args = parser.parse_args()
    app = QGuiApplication.instance() or QGuiApplication([])
    app.setApplicationName("SEOHEAD icon export")
    paths = render(ASSETS / "app" / "seohead.svg", args.output.resolve())
    if args.output.resolve() == (ASSETS / "app").resolve():
        manifest_path = ASSETS / "asset-manifest.json"
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        updated_paths = {path.relative_to(ASSETS).as_posix() for path in paths}
        entries = [entry for entry in entries if entry["path"] not in updated_paths]
        entries.extend(
            {
                "path": path.relative_to(ASSETS).as_posix(),
                "derived_from": "app/seohead.svg",
                "generator": "scripts/render_app_icon.py (Qt SVG; iconutil for ICNS)",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in paths
        )
        manifest_path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"icons": [path.name for path in paths]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
