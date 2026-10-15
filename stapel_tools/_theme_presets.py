"""``--theme``: the generated ``frontend/stapel.theme.json``.

``DEFAULT_THEME`` is ``@stapel/tokens``' own ``theme.default.json`` (the
version the scaffold pins), so a fresh project passes the package's contrast
gate as shipped; ``tests/test_theme_presets.py`` compares it with the sibling
checkout. A preset swaps only the private ``brand`` ramp; each one is checked
against the ``stapel-tokens`` bin (0 contrast errors) by the same test.
"""
from __future__ import annotations

import json
from pathlib import Path

TOKENS_VERSION = "0.8.1"

DEFAULT_THEME: dict = json.loads(r'''
{
  "ramps": {
    "brand": {
      "100": "#eef0fd",
      "300": "#98a5fa",
      "400": "#7c8cf8",
      "500": "#4657d9",
      "700": "#3948b8",
      "900": "#232b4d"
    }
  },
  "core": {
    "surface": {
      "light": "gray.25",
      "dark": "gray.950"
    },
    "surface-raised": {
      "light": "gray.25",
      "dark": "gray.850"
    },
    "surface-sunken": {
      "light": "gray.100",
      "dark": "gray.900"
    },
    "surface-overlay": {
      "light": "gray.25",
      "dark": "gray.850"
    },
    "text": {
      "light": "gray.900",
      "dark": "gray.100"
    },
    "text-muted": {
      "light": "gray.600",
      "dark": "gray.400"
    },
    "text-subtle": {
      "light": "gray.575",
      "dark": "gray.450"
    },
    "text-on-accent": {
      "light": "gray.25",
      "dark": "gray.900"
    },
    "border": {
      "light": "gray.400",
      "dark": "gray.500"
    },
    "border-subtle": {
      "light": "gray.300",
      "dark": "gray.550"
    },
    "focus-ring": {
      "light": "brand.500",
      "dark": "brand.300"
    },
    "brand": {
      "light": "brand.500",
      "dark": "brand.300"
    },
    "brand-hover": {
      "light": "brand.700",
      "dark": "brand.100"
    },
    "brand-active": {
      "light": "brand.900",
      "dark": "brand.100"
    },
    "brand-subtle": {
      "light": "brand.100",
      "dark": "brand.900"
    },
    "link": {
      "light": "brand.500",
      "dark": "brand.300"
    },
    "link-hover": {
      "light": "brand.700",
      "dark": "brand.100"
    },
    "success": {
      "light": "green.500",
      "dark": "green.300"
    },
    "success-bg": {
      "light": "green.100",
      "dark": "green.900"
    },
    "success-border": {
      "light": "green.300",
      "dark": "green.700"
    },
    "success-on": {
      "light": "gray.25",
      "dark": "gray.900"
    },
    "warning": {
      "light": "amber.500",
      "dark": "amber.300"
    },
    "warning-bg": {
      "light": "amber.100",
      "dark": "amber.900"
    },
    "warning-border": {
      "light": "amber.300",
      "dark": "amber.700"
    },
    "warning-on": {
      "light": "gray.25",
      "dark": "gray.900"
    },
    "error": {
      "light": "red.500",
      "dark": "red.300"
    },
    "error-bg": {
      "light": "red.100",
      "dark": "red.900"
    },
    "error-border": {
      "light": "red.300",
      "dark": "red.700"
    },
    "error-on": {
      "light": "gray.25",
      "dark": "gray.900"
    },
    "info": {
      "light": "blue.500",
      "dark": "blue.300"
    },
    "info-bg": {
      "light": "blue.100",
      "dark": "blue.900"
    },
    "info-border": {
      "light": "blue.300",
      "dark": "blue.700"
    },
    "info-on": {
      "light": "gray.25",
      "dark": "gray.900"
    }
  },
  "contrastExceptions": [
    {
      "fg": "border",
      "bg": "surface",
      "mode": "light",
      "reason": "border light stays gray.400 (2.0:1 on white) — the reference's light outlines are this light and the founder compares against it (owner decision 2026-09-14). Decorative under WCAG 1.4.11: cards, dialogs, sheets and the mega menu carry an elevation shadow for their boundary, tables and lists a fill on hover/selection, and the focused field its 3:1 focus-ring; the resting Input/Select outline is the one edge this is the sole boundary of, and that is the accepted trade"
    },
    {
      "fg": "border",
      "bg": "surface-raised",
      "mode": "light",
      "reason": "border light stays gray.400 (2.0:1 on white) — the reference's light outlines are this light and the founder compares against it (owner decision 2026-09-14). Decorative under WCAG 1.4.11: cards, dialogs, sheets and the mega menu carry an elevation shadow for their boundary, tables and lists a fill on hover/selection, and the focused field its 3:1 focus-ring; the resting Input/Select outline is the one edge this is the sole boundary of, and that is the accepted trade"
    },
    {
      "fg": "border",
      "bg": "surface-sunken",
      "mode": "light",
      "reason": "border light stays gray.400 (2.0:1 on white) — the reference's light outlines are this light and the founder compares against it (owner decision 2026-09-14). Decorative under WCAG 1.4.11: cards, dialogs, sheets and the mega menu carry an elevation shadow for their boundary, tables and lists a fill on hover/selection, and the focused field its 3:1 focus-ring; the resting Input/Select outline is the one edge this is the sole boundary of, and that is the accepted trade"
    },
    {
      "fg": "border",
      "bg": "surface-overlay",
      "mode": "light",
      "reason": "border light stays gray.400 (2.0:1 on white) — the reference's light outlines are this light and the founder compares against it (owner decision 2026-09-14). Decorative under WCAG 1.4.11: cards, dialogs, sheets and the mega menu carry an elevation shadow for their boundary, tables and lists a fill on hover/selection, and the focused field its 3:1 focus-ring; the resting Input/Select outline is the one edge this is the sole boundary of, and that is the accepted trade"
    },
    {
      "fg": "border-subtle",
      "bg": "surface",
      "mode": "light",
      "reason": "border-subtle light stays gray.300 (1.4:1 on white) — the reference's light dividers are this light and the founder compares against it (owner decision 2026-09-14). Decorative under WCAG 1.4.11: it is only ever a separator between rows, panes and menu sections whose content and spacing already carry the grouping; no component's boundary is this line alone"
    },
    {
      "fg": "border-subtle",
      "bg": "surface-raised",
      "mode": "light",
      "reason": "border-subtle light stays gray.300 (1.4:1 on white) — the reference's light dividers are this light and the founder compares against it (owner decision 2026-09-14). Decorative under WCAG 1.4.11: it is only ever a separator between rows, panes and menu sections whose content and spacing already carry the grouping; no component's boundary is this line alone"
    },
    {
      "fg": "border-subtle",
      "bg": "surface-sunken",
      "mode": "light",
      "reason": "border-subtle light stays gray.300 (1.4:1 on white) — the reference's light dividers are this light and the founder compares against it (owner decision 2026-09-14). Decorative under WCAG 1.4.11: it is only ever a separator between rows, panes and menu sections whose content and spacing already carry the grouping; no component's boundary is this line alone"
    },
    {
      "fg": "border-subtle",
      "bg": "surface-overlay",
      "mode": "light",
      "reason": "border-subtle light stays gray.300 (1.4:1 on white) — the reference's light dividers are this light and the founder compares against it (owner decision 2026-09-14). Decorative under WCAG 1.4.11: it is only ever a separator between rows, panes and menu sections whose content and spacing already carry the grouping; no component's boundary is this line alone"
    }
  ],
  "_contrastExceptionsComment": "Documented per-pairing escape hatch for the contrast GATE (src/gen/contrast.mjs CONTRAST_PAIRS, checked by validateTheme in src/gen/lib.mjs). The gate is a cross product since 2026-09-14 (text family x every fill, chrome x every surface, both modes); every text pair passes in both modes, and the entries here are the two DECORATIVE chrome roles in light (border 2.0:1, border-subtle 1.4:1 on white), pinned to the reference's light outlines by owner decision; each reason names what carries the boundary instead, and the stale-exception check deletes an entry the moment its pair passes. Format: { \"fg\": \"<role>\", \"bg\": \"<role>\", \"mode\": \"light\"|\"dark\", \"reason\": \"<why this pairing is intentionally exempt>\" }. `reason` is required (non-empty) — an exception can never be silent. An entry that stops failing (pairing now legit) or that is malformed is itself a validation ERROR — the list must only ever hold live, reasoned exceptions.",
  "scales": {
    "fontFamily": {
      "sans": "'Inter', 'Helvetica Neue', Helvetica, Arial, system-ui, sans-serif",
      "mono": "'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace"
    },
    "fontSize": {
      "xs": {
        "fontSize": 12,
        "lineHeight": 16
      },
      "sm": {
        "fontSize": 14,
        "lineHeight": 20
      },
      "md": {
        "fontSize": 16,
        "lineHeight": 24
      },
      "lg": {
        "fontSize": 18,
        "lineHeight": 28
      },
      "xl": {
        "fontSize": 22,
        "lineHeight": 30
      },
      "2xl": {
        "fontSize": 28,
        "lineHeight": 36
      },
      "3xl": {
        "fontSize": 36,
        "lineHeight": 44
      }
    },
    "fontWeight": {
      "regular": 400,
      "medium": 500,
      "semibold": 600,
      "bold": 700
    },
    "spacing": {
      "0": 0,
      "1": 4,
      "2": 8,
      "3": 12,
      "4": 16,
      "5": 24,
      "6": 32,
      "7": 48,
      "8": 64
    },
    "radii": {
      "none": 0,
      "sm": 4,
      "md": 8,
      "lg": 12,
      "xl": 20,
      "full": 9999
    },
    "_controlsComment": "Control SHAPE axis (owner escalation 2026-09-02): the height every design-system bridge hands its controls. `height` = the wide seed (antd's own 32); `height-phone` = the fleet's phone touch floor (WCAG 2.5.8 / platform HIGs: 44). Emitted as --stapel-control-height / --stapel-control-height-phone; bridges read them LIVE off the document like every colour role, so a host overrides control height by editing this scale and regenerating. Control radius/font-size need no axis of their own — the bridges read the existing --stapel-radius-md / --stapel-font-size-md live (one dictionary, no twins).",
    "controls": {
      "height": 32,
      "height-phone": 44
    },
    "_responsiveComment": "A scale role whose value changes with the VIEWPORT, authored as SPACING STEP names so it stays on the scale a theme retunes in one place. Emitted mobile-first: the narrowest breakpoint is the base declaration and every wider one is an @media arm. `page-gutter` is how far a page's content sits from the edge of the window — one number cannot serve both a 390px phone (where 24px is a tenth of the screen) and a 1440px desktop, and each component guessing for itself is what put three different left edges down one composed page.",
    "responsive": {
      "page-gutter": {
        "phone": "1",
        "tablet": "2",
        "desktop": "5"
      }
    },
    "breakpoints": {
      "phone": 0,
      "tablet": 768,
      "desktop": 1200
    }
  },
  "elevation": {
    "none": {
      "light": "none",
      "dark": "none"
    },
    "low": {
      "light": "0 1px 2px rgba(15, 18, 24, 0.08)",
      "dark": "0 1px 2px rgba(0, 0, 0, 0.4)"
    },
    "medium": {
      "light": "0 2px 8px rgba(15, 18, 24, 0.12)",
      "dark": "0 2px 8px rgba(0, 0, 0, 0.5)"
    },
    "high": {
      "light": "0 8px 24px rgba(15, 18, 24, 0.16)",
      "dark": "0 8px 24px rgba(0, 0, 0, 0.6)"
    }
  }
}
''')

#: preset name -> brand ramp. ``default`` keeps the package's own ramp.
THEME_PRESETS: dict[str, dict[str, str] | None] = {
    "default": None,
    "ocean": {"100": "#e6f2fb", "300": "#7cc0ef", "400": "#4fa6e3", "500": "#0f63a3", "700": "#0b4c7e", "900": "#0f2a40"},
    "forest": {"100": "#e7f5ec", "300": "#79cf9a", "400": "#4fb877", "500": "#1b7a43", "700": "#145e33", "900": "#12301f"},
    "crimson": {"100": "#fdecee", "300": "#f59aa6", "400": "#ef7385", "500": "#b4233c", "700": "#8d1a2e", "900": "#3f1219"},
    "graphite": {"100": "#eef0f3", "300": "#a9b2c0", "400": "#8d97a8", "500": "#3f4a5c", "700": "#2e3746", "900": "#1b2029"},
    "amber": {"100": "#fdf3e1", "300": "#f5c46b", "400": "#eeac3a", "500": "#8f5600", "700": "#6e4200", "900": "#3a2405"},
}

_COMMENT = (
    "{title}'s colour tokens (Sec.68 neutral colour-role dictionary, theme "
    "preset: {preset}). SOURCE OF TRUTH for every colour this project uses -- "
    "code never hardcodes hex/rgb (no-raw-colors). Compiled by @stapel/tokens' "
    "own `stapel-tokens` bin (never a vendored copy -- see package.json's "
    "gen:tokens scripts + the tokens-check pre-commit hook). Edit `ramps`/`core` "
    "below to re-theme; `npm run gen:tokens:check` keeps the contrast gate."
)


def theme_choices() -> list[str]:
    return list(THEME_PRESETS)


def render_theme_json(title: str, theme: str | None = None) -> str:
    """A preset name, or a path to a host theme file (copied as is)."""
    theme = theme or "default"
    if theme not in THEME_PRESETS:
        path = Path(theme)
        if not path.is_file():
            raise ValueError(
                f"--theme {theme!r}: not a preset ({', '.join(THEME_PRESETS)}) "
                "and not a file"
            )
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not ({"core", "ramps"} & set(data)):
            raise ValueError(f"--theme {theme}: a theme file carries `ramps` and/or `core`")
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    data = json.loads(json.dumps(DEFAULT_THEME))
    ramp = THEME_PRESETS[theme]
    if ramp is not None:
        data["ramps"]["brand"] = dict(ramp)
    out = {"_comment": _COMMENT.format(title=title, preset=theme), **data}
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"
