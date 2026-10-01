"""Static SVG charts (bars, lines, donut) for print reports, written without a plotting library.

Each function returns one self-contained ``<svg>`` string in CSS-pixel units. The charts only
place values they are given: they compute geometry (scales, tick steps, label positions), never a
statistic. Value labels are placed only where they cannot collide; when a row of labels would
overlap, every n-th label is kept (always including the largest value) rather than drawing text
over text.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape

Formatter = Callable[[float], str]
CHAR_WIDTH = 0.58  # average glyph advance as a share of font size, for label collision checks


@dataclass(frozen=True)
class Series:
    name: str
    values: Sequence[float | None]
    color: str
    dashed: bool = False
    width: float = 2.4
    labels: bool = True


def text_width(text: str, size: float) -> float:
    return len(text) * size * CHAR_WIDTH


def nice_ceiling(value: float) -> float:
    """The smallest 1/2/2.5/5 x 10^k at or above ``value`` (1 for an all-zero chart)."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    base = 10**exponent
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * base:
            return step * base
    return 10 * base


def _ticks(maximum: float, count: int = 4) -> list[float]:
    """Axis ticks from zero; the step is at least 1 because every chart here plots counts."""
    top = nice_ceiling(maximum)
    step = max(nice_ceiling(top / count), 1.0)
    ticks = []
    tick = 0.0
    while tick <= top + step / 1000:
        ticks.append(round(tick, 6))
        tick += step
    return ticks


def _stride(slot: float, widest: float, gap: float = 6.0) -> int:
    """How many slots one label needs so neighbours do not overlap."""
    if slot <= 0:
        return 1
    return max(1, math.ceil((widest + gap) / slot))


def _frame(width: float, height: float, body: list[str], title: str | None) -> str:
    label = f' aria-label="{escape(title)}" role="img"' if title else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}"{label}>' + "".join(body) + "</svg>"
    )


def _axes(
    x0: float,
    y0: float,
    plot_w: float,
    plot_h: float,
    ticks: list[float],
    fmt: Formatter,
    *,
    font: float,
    grid: str,
    ink: str,
) -> list[str]:
    parts = []
    top = ticks[-1] or 1
    for tick in ticks:
        y = y0 + plot_h - tick / top * plot_h
        parts.append(
            f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x0 + plot_w:.1f}" y2="{y:.1f}" '
            f'stroke="{grid}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x0 - 8:.1f}" y="{y + font * 0.35:.1f}" text-anchor="end" '
            f'font-size="{font:.1f}" fill="{ink}">{escape(fmt(tick))}</text>'
        )
    return parts


def _x_labels(
    labels: Sequence[str],
    centers: list[float],
    y: float,
    slot: float,
    *,
    font: float,
    ink: str,
) -> list[str]:
    if not labels:
        return []
    widest = max(text_width(label, font) for label in labels)
    stride = _stride(slot, widest)
    parts = []
    last = len(labels) - 1
    for index, (label, x) in enumerate(zip(labels, centers, strict=True)):
        # Keep a regular rhythm from the first label; the final label is kept only when it
        # does not crowd the previous kept one.
        keep = index % stride == 0 or (index == last and (last % stride) >= stride / 2)
        if not keep:
            continue
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" font-size="{font:.1f}" '
            f'fill="{ink}">{escape(label)}</text>'
        )
    return parts


def bar_chart(
    labels: Sequence[str],
    series: Sequence[Series],
    *,
    width: float,
    height: float,
    fmt: Formatter,
    axis_fmt: Formatter | None = None,
    ink: str = "#334155",
    grid: str = "#E2E8F0",
    font: float = 11.0,
    title: str | None = None,
) -> str:
    """Vertical bars, grouped when several series share the labels."""
    axis_fmt = axis_fmt or fmt
    values = [v for s in series for v in s.values if v is not None]
    maximum = max(values, default=0.0)
    ticks = _ticks(maximum)
    top = ticks[-1] or 1
    y_label_w = max(text_width(axis_fmt(t), font) for t in ticks) + 14
    x0, y0 = y_label_w, font * 2.2
    plot_w = width - x0 - 6
    plot_h = height - y0 - font * 2.6
    count = max(len(labels), 1)
    slot = plot_w / count
    group_w = slot * (0.72 if len(series) == 1 else 0.8)
    bar_w = group_w / max(len(series), 1)
    parts = _axes(x0, y0, plot_w, plot_h, ticks, axis_fmt, font=font, grid=grid, ink=ink)
    centers = [x0 + slot * (i + 0.5) for i in range(len(labels))]
    label_font = font * 0.92
    for s_index, item in enumerate(series):
        texts = [fmt(v) if v is not None else "" for v in item.values]
        widest = max((text_width(t, label_font) for t in texts), default=0)
        horizontal = widest + 2 <= bar_w * (1.0 if len(series) == 1 else 1.15) or (
            len(series) == 1 and widest + 4 <= slot
        )
        vertical = not horizontal and bar_w >= label_font * 1.05
        stride = 1 if horizontal or vertical else _stride(slot, widest)
        peak = max(range(len(item.values)), key=lambda i: item.values[i] or 0, default=-1)
        for index, value in enumerate(item.values):
            if value is None:
                continue
            h = max(value / top * plot_h, 0.0)
            x = centers[index] - group_w / 2 + bar_w * s_index
            y = y0 + plot_h - h
            parts.append(
                f'<rect x="{x + 0.6:.1f}" y="{y:.1f}" width="{max(bar_w - 1.2, 0.8):.1f}" '
                f'height="{h:.1f}" rx="{min(2.5, bar_w / 4):.1f}" fill="{item.color}"/>'
            )
            # A zero bar has no height to label; the axis already says 0.
            if not item.labels or value == 0 or (index % stride and index != peak):
                continue
            cx = x + bar_w / 2
            if horizontal:
                parts.append(
                    f'<text x="{cx:.1f}" y="{y - 4:.1f}" text-anchor="middle" '
                    f'font-size="{label_font:.1f}" font-weight="600" fill="{ink}">'
                    f"{escape(texts[index])}</text>"
                )
            elif vertical:
                tw = text_width(texts[index], label_font)
                inside = h >= tw + 8
                ty = y + 5 if inside else y - 4
                anchor = "end" if inside else "start"
                colour = "#FFFFFF" if inside else ink
                parts.append(
                    f'<text transform="translate({cx + label_font * 0.35:.1f},{ty:.1f}) '
                    f'rotate(-90)" text-anchor="{anchor}" font-size="{label_font:.1f}" '
                    f'font-weight="600" fill="{colour}">{escape(texts[index])}</text>'
                )
            else:
                parts.append(
                    f'<text x="{cx:.1f}" y="{y - 4:.1f}" text-anchor="middle" '
                    f'font-size="{label_font:.1f}" font-weight="600" fill="{ink}">'
                    f"{escape(texts[index])}</text>"
                )
    parts.append(
        f'<line x1="{x0:.1f}" y1="{y0 + plot_h:.1f}" x2="{x0 + plot_w:.1f}" '
        f'y2="{y0 + plot_h:.1f}" stroke="{ink}" stroke-width="1"/>'
    )
    parts += _x_labels(labels, centers, y0 + plot_h + font * 1.6, slot, font=font, ink=ink)
    return _frame(width, height, parts, title)


def line_chart(
    labels: Sequence[str],
    series: Sequence[Series],
    *,
    width: float,
    height: float,
    fmt: Formatter,
    axis_fmt: Formatter | None = None,
    ink: str = "#334155",
    grid: str = "#E2E8F0",
    font: float = 11.0,
    title: str | None = None,
) -> str:
    """Lines over shared x positions; the first series gets an area tint and value labels."""
    axis_fmt = axis_fmt or fmt
    values = [v for s in series for v in s.values if v is not None]
    maximum = max(values, default=0.0)
    ticks = _ticks(maximum)
    top = ticks[-1] or 1
    y_label_w = max(text_width(axis_fmt(t), font) for t in ticks) + 14
    x0, y0 = y_label_w, font * 2.2
    plot_w = width - x0 - 14
    plot_h = height - y0 - font * 2.6
    count = max(len(labels), 1)
    step = plot_w / max(count - 1, 1)
    xs = [x0 + step * i if count > 1 else x0 + plot_w / 2 for i in range(len(labels))]
    parts = _axes(x0, y0, plot_w, plot_h, ticks, axis_fmt, font=font, grid=grid, ink=ink)

    def point(index: int, value: float) -> tuple[float, float]:
        return xs[index], y0 + plot_h - value / top * plot_h

    for s_index in reversed(range(len(series))):
        item = series[s_index]
        runs: list[list[tuple[float, float]]] = [[]]
        for index, value in enumerate(item.values[: len(xs)]):
            if value is None:
                if runs[-1]:
                    runs.append([])
                continue
            runs[-1].append(point(index, value))
        for run in runs:
            if not run:
                continue
            path = " ".join(f"{x:.1f},{y:.1f}" for x, y in run)
            if s_index == 0 and len(run) > 1:
                base = y0 + plot_h
                area = f"{run[0][0]:.1f},{base:.1f} {path} {run[-1][0]:.1f},{base:.1f}"
                parts.append(f'<polygon points="{area}" fill="{item.color}" fill-opacity="0.08"/>')
            dash = ' stroke-dasharray="6 4"' if item.dashed else ""
            parts.append(
                f'<polyline points="{path}" fill="none" stroke="{item.color}" '
                f'stroke-width="{item.width:.1f}" stroke-linejoin="round" '
                f'stroke-linecap="round"{dash}/>'
            )
            if len(run) == 1:
                x, y = run[0]
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{item.color}"/>')

    if series and series[0].labels:
        parts += _line_labels(series[0], xs, point, fmt, font=font, ink=ink, x_max=x0 + plot_w)
    parts.append(
        f'<line x1="{x0:.1f}" y1="{y0 + plot_h:.1f}" x2="{x0 + plot_w:.1f}" '
        f'y2="{y0 + plot_h:.1f}" stroke="{ink}" stroke-width="1"/>'
    )
    parts += _x_labels(labels, xs, y0 + plot_h + font * 1.6, step, font=font, ink=ink)
    return _frame(width, height, parts, title)


def _line_labels(
    item: Series,
    xs: list[float],
    point: Callable[[int, float], tuple[float, float]],
    fmt: Formatter,
    *,
    font: float,
    ink: str,
    x_max: float,
) -> list[str]:
    """Label the peak, the trough and the last point of a line without overlapping text."""
    indexed = [(i, v) for i, v in enumerate(item.values[: len(xs)]) if v is not None]
    if not indexed:
        return []
    peak = max(indexed, key=lambda pair: pair[1])
    if peak[1] == 0:
        return []  # a flat zero line needs no value labels; the axis already says 0
    trough = min(indexed, key=lambda pair: pair[1])
    last = indexed[-1]
    chosen: list[tuple[int, float, str]] = [(peak[0], peak[1], "above")]
    for candidate, side in ((last, "above"), (trough, "below")):
        if candidate[0] not in {c[0] for c in chosen}:
            chosen.append((candidate[0], candidate[1], side))
    size = font * 0.95
    placed: list[tuple[float, float, float, float]] = []
    parts = []
    for index, value, side in chosen:
        x, y = point(index, value)
        text = fmt(value)
        w = text_width(text, size)
        left = min(max(x - w / 2, xs[0] - 4), x_max - w)
        ty = y - 8 if side == "above" else y + size + 6
        box = (left - 2, ty - size, left + w + 2, ty + 3)
        if any(
            not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3]) for b in placed
        ):
            continue
        placed.append(box)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{item.color}"/>')
        parts.append(
            f'<text x="{left:.1f}" y="{ty:.1f}" font-size="{size:.1f}" font-weight="600" '
            f'fill="{ink}">{escape(text)}</text>'
        )
    return parts


def donut_chart(
    values: Sequence[float],
    colors: Sequence[str],
    labels: Sequence[str],
    *,
    size: float,
    center_value: str | None = None,
    center_label: str | None = None,
    ink: str = "#334155",
    font: float = 11.0,
    min_label_share: float = 0.045,
    title: str | None = None,
) -> str:
    """A ring of segments; segment labels are drawn only on segments wide enough to hold them."""
    total = sum(v for v in values if v and v > 0)
    cx = cy = size / 2
    outer = size / 2 - 2
    inner = outer * 0.58
    parts: list[str] = []
    if total <= 0:
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{(outer + inner) / 2:.1f}" fill="none" '
            f'stroke="#E2E8F0" stroke-width="{outer - inner:.1f}"/>'
        )
        return _frame(size, size, parts, title)
    angle = -math.pi / 2
    shares = [max(v or 0, 0) / total for v in values]
    for share, color, label in zip(shares, colors, labels, strict=True):
        if share <= 0:
            continue
        sweep = share * 2 * math.pi
        if share >= 0.9999:
            parts.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{(outer + inner) / 2:.1f}" fill="none" '
                f'stroke="{color}" stroke-width="{outer - inner:.1f}"/>'
            )
        else:
            end = angle + sweep
            large = 1 if sweep > math.pi else 0
            p = [
                (cx + outer * math.cos(angle), cy + outer * math.sin(angle)),
                (cx + outer * math.cos(end), cy + outer * math.sin(end)),
                (cx + inner * math.cos(end), cy + inner * math.sin(end)),
                (cx + inner * math.cos(angle), cy + inner * math.sin(angle)),
            ]
            parts.append(
                f'<path d="M{p[0][0]:.2f},{p[0][1]:.2f} A{outer:.2f},{outer:.2f} 0 {large} 1 '
                f"{p[1][0]:.2f},{p[1][1]:.2f} L{p[2][0]:.2f},{p[2][1]:.2f} "
                f'A{inner:.2f},{inner:.2f} 0 {large} 0 {p[3][0]:.2f},{p[3][1]:.2f} Z" '
                f'fill="{color}" stroke="#FFFFFF" stroke-width="1.5"/>'
            )
        if share >= min_label_share and label:
            mid = angle + sweep / 2
            radius = (outer + inner) / 2
            tx, ty = cx + radius * math.cos(mid), cy + radius * math.sin(mid)
            parts.append(
                f'<text x="{tx:.1f}" y="{ty + font * 0.35:.1f}" text-anchor="middle" '
                f'font-size="{font:.1f}" font-weight="700" fill="#FFFFFF">{escape(label)}</text>'
            )
        angle += sweep
    if center_value:
        parts.append(
            f'<text x="{cx:.1f}" y="{cy + font * 0.2:.1f}" text-anchor="middle" '
            f'font-size="{font * 1.9:.1f}" font-weight="700" fill="{ink}">'
            f"{escape(center_value)}</text>"
        )
    if center_label:
        parts.append(
            f'<text x="{cx:.1f}" y="{cy + font * 1.6:.1f}" text-anchor="middle" '
            f'font-size="{font * 0.9:.1f}" fill="{ink}" fill-opacity="0.7">'
            f"{escape(center_label)}</text>"
        )
    return _frame(size, size, parts, title)
