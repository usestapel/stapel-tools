"""SVG wireframes of a resolved navigation plan (navigation-templates spec §3.9, §5.1).

``stapel-nav-plan --svg <dir>`` writes one wireframe per platform, audience and
size class, drawn from the plan alone: the header and its slots, the carrier
(bottom tabs, island, top tabs, rail, side panel, drawer, stack) with the
destinations it shows and its "More", the guest's sign-in entry. No browser and
no model: the same plan gives the same bytes, so Studio can show a client their
own composition before anything is generated.

What a wireframe cannot show: the skin (colours, type, icons), the content of a
page, and destinations reached only from another screen.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional
from xml.sax.saxutils import escape

#: Canvas per size class: a phone, a tablet, a desktop window.
CANVAS: dict[str, tuple[int, int]] = {
    "compact": (390, 844),
    "medium": (900, 1100),
    "expanded": (1440, 900),
}

#: Header slots in their drawing order (start -> end).
HEADER_SLOTS: tuple[str, ...] = (
    "brand", "browse", "search", "primaryAction", "language", "theme", "notifications", "cart",
    "account",
)

_HEADER_H = 56
_TABS_H = 44
_BOTTOM_H = 64
_RAIL_W = 80
_PANEL_W = 240
_FOOTER_H = 40
_FONT = "font-family='system-ui, sans-serif'"


def label_for(dest_id: str, labels: Optional[Mapping[str, str]] = None) -> str:
    """The text a wireframe writes for a destination: the caller's label when
    given (Studio passes the client's), else the id's last segment in words."""
    if labels and dest_id in labels:
        return labels[dest_id]
    head, _, tail = dest_id.rpartition(".")
    if tail in ("root", "home", "index") and head:
        tail = head.rsplit(".", 1)[-1]
    return tail.replace("_", " ").replace("-", " ").capitalize()


def _slot_label(slot: str, value: str, audience: str, sign_in: Optional[str]) -> str:
    if slot == "account":
        return "Sign in" if audience == "guest" and sign_in else "Account"
    if slot == "brand":
        return "Brand"
    return {"primaryAction": "Action", "notifications": "Bell"}.get(slot, slot.capitalize())


def _rect(x: float, y: float, w: float, h: float, cls: str, rx: int = 0) -> str:
    return (f"<rect class='{cls}' x='{x:g}' y='{y:g}' width='{w:g}' height='{h:g}'"
            + (f" rx='{rx}'" if rx else "") + "/>")


def _text(x: float, y: float, text: str, cls: str = "t", anchor: str = "middle") -> str:
    return (f"<text class='{cls}' x='{x:g}' y='{y:g}' text-anchor='{anchor}' "
            f"dominant-baseline='middle'>{escape(text)}</text>")


def _row(items: list[str], x: float, y: float, w: float, h: float, cls: str) -> list[str]:
    """*items* as equal cells across a bar."""
    out: list[str] = []
    if not items:
        return out
    cell = w / len(items)
    for i, item in enumerate(items):
        cx = x + cell * i
        out.append(_rect(cx + 4, y + 6, cell - 8, h - 12, cls, rx=6))
        out.append(_text(cx + cell / 2, y + h / 2, item))
    return out


def _column(items: list[str], x: float, y: float, w: float, item_h: float, cls: str) -> list[str]:
    out: list[str] = []
    for i, item in enumerate(items):
        top = y + 8 + i * (item_h + 6)
        out.append(_rect(x + 8, top, w - 16, item_h, cls, rx=6))
        out.append(_text(x + w / 2, top + item_h / 2, item))
    return out


def render(  # noqa: C901
    resolved: Mapping, audience: str, size_class: str, *,
    labels: Optional[Mapping[str, str]] = None, title: str = "",
) -> str:
    """One wireframe of *resolved* (one platform's ``navigation.resolved``)."""
    width, height = CANVAS[size_class]
    view = resolved["audiences"][audience][size_class]
    carrier = view.get("carrier") or "stack"
    items = [label_for(d, labels) for d in view.get("visible", [])]
    if view.get("more"):
        items.append("More")
    header = resolved.get("header") or "none"
    slots = resolved.get("slots") or {}
    sign_in = resolved.get("signIn")
    body: list[str] = [_rect(0, 0, width, height, "page")]
    top, left, bottom, right = 0.0, 0.0, float(height), float(width)

    if header != "none":
        body.append(_rect(0, 0, width, _HEADER_H, "bar"))
        present = [s for s in HEADER_SLOTS if slots.get(s) or (s == "account" and sign_in)]
        start = [s for s in present if s in ("brand", "browse")]
        end = [s for s in present if s not in start and s != "search"]
        x = 12.0
        for slot in start:
            text = _slot_label(slot, slots.get(slot) or "", audience, sign_in)
            if slot == "brand" and title:
                text = title
            body.append(_rect(x, 12, 110, 32, "slot", rx=6))
            body.append(_text(x + 55, 28, text))
            x += 122
        xr = width - 12.0
        for slot in reversed(end):
            text = _slot_label(slot, slots.get(slot) or "", audience, sign_in)
            w = 84 if slot == "account" else 64
            xr -= w
            body.append(_rect(xr, 12, w, 32, "slot", rx=6))
            body.append(_text(xr + w / 2, 28, text))
            xr -= 8
        if "search" in present and xr - x > 120:
            body.append(_rect(x, 12, xr - x, 32, "slot", rx=16))
            body.append(_text(x + 16, 28, "Search", anchor="start"))
        top = float(_HEADER_H)

    if slots.get("footer") and size_class != "compact":
        bottom -= _FOOTER_H
        body.append(_rect(0, bottom, width, _FOOTER_H, "bar"))
        body.append(_text(width / 2, bottom + _FOOTER_H / 2, "Footer"))

    if carrier in ("bottom-tabs", "island"):
        h = _BOTTOM_H
        if carrier == "island":
            pad = 16
            body.append(_rect(pad, bottom - h - pad, width - 2 * pad, h, "carrier", rx=h // 2))
            body += _row(items, pad + 8, bottom - h - pad, width - 2 * pad - 16, h, "item")
            bottom -= h + 2 * pad
        else:
            body.append(_rect(0, bottom - h, width, h, "carrier"))
            body += _row(items, 0, bottom - h, width, h, "item")
            bottom -= h
    elif carrier == "top-tabs":
        body.append(_rect(0, top, width, _TABS_H, "carrier"))
        body += _row(items, 0, top, min(width, 160 * len(items) or width), _TABS_H, "item")
        top += _TABS_H
    elif carrier in ("rail", "side-panel"):
        w = _RAIL_W if carrier == "rail" else _PANEL_W
        body.append(_rect(0, top, w, bottom - top, "carrier"))
        body += _column(items, 0, top, w, 48 if carrier == "rail" else 36, "item")
        left = float(w)
    elif carrier == "drawer":
        body.append(_rect(12, top + 12 if header != "none" else 12, 40, 32, "slot", rx=6))
        body.append(_text(32, (top + 28) if header != "none" else 28, "Menu"))
        drawer_w = min(_PANEL_W + 40, width * 0.75)
        body.append(_rect(0, top, drawer_w, bottom - top, "drawer"))
        body += _column(items, 0, top + 44, drawer_w, 40, "item")
        left = float(drawer_w)
    elif carrier == "stack":
        body.append(_text(width / 2, top + 20, "(one destination: no carrier)", cls="note"))

    body.append(_rect(left + 16, top + 16, right - left - 32, bottom - top - 32, "content", rx=8))
    body.append(_text((left + right) / 2, (top + bottom) / 2,
                      f"{audience} · {size_class} · {carrier}", cls="note"))

    style = (
        "<style>"
        ".page{fill:#ffffff}.bar{fill:#f1f3f5;stroke:#ced4da}.slot{fill:#ffffff;stroke:#adb5bd}"
        ".carrier{fill:#e7f0fe;stroke:#74a9f7}.drawer{fill:#f8f9fa;stroke:#adb5bd}"
        ".item{fill:#ffffff;stroke:#74a9f7}.content{fill:#fafafa;stroke:#dee2e6;stroke-dasharray:6 4}"
        ".t{font-size:13px;fill:#212529}.note{font-size:13px;fill:#868e96}"
        "</style>"
    )
    caption = " ".join(p for p in (resolved.get("platform", ""), audience, size_class) if p)
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"viewBox='0 0 {width} {height}' {_FONT}>"
        f"<title>{escape(caption)}</title>"
        + style + "".join(body) + "</svg>\n"
    )


def write_wireframes(
    plan: Mapping[str, Mapping], out_dir: Path, *,
    labels: Optional[Mapping[str, str]] = None, title: str = "",
) -> list[Path]:
    """``<platform>-<audience>-<size class>.svg`` for every platform of *plan*
    (``plan_navigation``'s ``{platform: resolved}``). Returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for platform, resolved in sorted(plan.items()):
        for audience in sorted(resolved["audiences"]):
            for size_class in CANVAS:
                if size_class not in resolved["audiences"][audience]:
                    continue
                path = out_dir / f"{platform}-{audience}-{size_class}.svg"
                path.write_text(render(resolved, audience, size_class, labels=labels, title=title),
                                encoding="utf-8")
                written.append(path)
    return written
