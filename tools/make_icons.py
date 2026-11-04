#!/usr/bin/env python3
"""design/pwa-icon.svg -> static/icons/*.png (PWA + iOS + favicon).

Why a script instead of checked-in only: the artwork lives in one SVG, and the
five sizes must agree. This box has no ImageMagick or librsvg, and the snap
Chromium is broken (`snap-confine`), so rasterising goes through the same
chrome-headless-shell binary tests/ and tools/shot.js already use.

Maskable: Android crops a "maskable" icon to a circle covering the middle 80%
of the square, so that variant renders the art at 78% around the centre and
leaves the background bleeding to all four edges. Rendering the normal artwork
as maskable would shave the gauge's ends off.

Run: .venv/bin/python tools/make_icons.py        (writes static/icons/)
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SVG = HERE / "design" / "pwa-icon.svg"
OUT = HERE / "static" / "icons"

BIN = Path(os.environ.get(
    "F1_CHROME",
    Path.home() / ".local/share/f1-agentic-analyst/tools/chrome-headless-shell-linux64/chrome-headless-shell",
))

#: (dosya adı, piksel, sanat ölçeği). Sanat ölçeği 1.0 = tuvalin tamamı.
TARGETS: tuple[tuple[str, int, float], ...] = (
    ("icon-192.png", 192, 1.0),
    ("icon-512.png", 512, 1.0),
    ("icon-maskable-512.png", 512, 0.78),
    ("apple-touch-icon-180.png", 180, 1.0),
    ("favicon-32.png", 32, 1.0),
)


def _svg_inner() -> str:
    """The <svg> element itself, without the XML declaration."""
    text = SVG.read_text(encoding="utf-8")
    m = re.search(r"<svg\b.*</svg>", text, re.S)
    if not m:
        raise SystemExit("design/pwa-icon.svg içinde <svg> bulunamadı")
    return m.group(0)


def _page(svg: str, scale: float) -> str:
    # preserveAspectRatio + viewBox do the scaling; no JS, no fonts, so the
    # rendering cannot depend on anything but the file itself.
    offset = (1 - scale) * 100 / 2
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
        "html,body{margin:0;padding:0;width:100%;height:100%;background:#0a0c10;overflow:hidden}"
        "svg{display:block;width:100%;height:100%}"
        "</style></head><body>"
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" '
        f'width="100%" height="100%" preserveAspectRatio="xMidYMid meet">'
        f'<g transform="translate(256 256) scale({scale}) translate(-256 -256)">'
        f"{_inner(svg)}</g></svg>"
        "</body></html>"
    )


def _inner(svg: str) -> str:
    return re.sub(r"^<svg\b[^>]*>|</svg>$", "", svg, flags=re.S)


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path} PNG değil")
    return struct.unpack(">II", data[16:24])


def main() -> int:
    if not BIN.is_file():
        print(f"chrome bulunamadı: {BIN}", file=sys.stderr)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    svg = _svg_inner()
    with tempfile.TemporaryDirectory(prefix="f1icons-") as tmp:
        page = Path(tmp) / "icon.html"
        page.write_text(_page(svg, 1.0), encoding="utf-8")
        for name, px, scale in TARGETS:
            page.write_text(_page(svg, scale), encoding="utf-8")
            dst = OUT / name
            subprocess.run(
                [str(BIN), "--headless", "--no-sandbox", "--disable-gpu",
                 "--hide-scrollbars", "--force-device-scale-factor=1",
                 f"--window-size={px},{px}", f"--screenshot={dst}",
                 page.as_uri()],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            w, h = _png_size(dst)
            if (w, h) != (px, px):
                print(f"HATA {name}: {w}x{h}, {px}x{px} bekleniyordu", file=sys.stderr)
                return 1
            print(f"{dst.relative_to(HERE)}  {w}x{h}  {dst.stat().st_size} bayt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
