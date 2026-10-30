"""WCAG contrast for the design-system colour roles, in every theme (DS-01).

Resolves the roles from static/css/system.css (tools/design_tokens.py), then:

* ENFORCED (exit 1 below the floor):
  - every text role on --c-surface, and the status-badge recipe
    (--c-<tone>-text on its 22% tint over --c-surface): 4.5:1;
  - --c-on-accent on --c-accent (text on an accent button): 4.5:1;
  - --c-focus on --c-surface and on --c-page: 3:1.
* REPORTED (WCAG 1.4.11 asks 3:1 only where the edge is the sole cue, so these
  are listed, not enforced): hairlines, HP-tier fills and status-fill borders
  on --c-surface.

Translucent colours (rgba, color-mix with transparent) are composited over the
surface they sit on before the ratio is taken.

Usage: python tools/check_contrast.py [--json]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_tokens  # noqa: E402

TEXT_ROLES = ["--c-text-1", "--c-text-2", "--c-text-3", "--c-text-on-surface-1",
              "--c-text-on-surface-2", "--c-text-on-surface-3", "--c-accent",
              "--c-danger-text", "--c-success-text", "--c-warn-text", "--c-info"]
STATUS_TONES = ["danger", "success", "warn"]
EDGES = ["--c-line", "--c-line-strong", "--c-line-neutral"]
HP = ["--c-hp-ok", "--c-hp-hurt", "--c-hp-bloodied", "--c-hp-crit", "--c-hp-down"]


def parse_color(value: str):
    """Return (r, g, b, a) with r/g/b in 0-255 and a in 0-1."""
    v = value.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (r, g, b, a)
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)", v)
    if m:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)),
                float(m.group(4)) if m.group(4) is not None else 1.0)
    m = re.fullmatch(r"color-mix\(\s*in srgb\s*,\s*(.+?)\s+([\d.]+)%\s*,\s*transparent\s*\)", v)
    if m:
        r, g, b, a = parse_color(m.group(1))
        return (r, g, b, a * float(m.group(2)) / 100)
    raise ValueError(f"unsupported colour: {value!r}")


def over(top, bottom):
    """Composite a (possibly translucent) colour over an opaque one."""
    r, g, b, a = top
    br, bg, bb, _ = bottom
    return (r * a + br * (1 - a), g * a + bg * (1 - a), b * a + bb * (1 - a), 1.0)


def luminance(c) -> float:
    def channel(x):
        x = x / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b, _ = c
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def ratio(fg, bg) -> float:
    la, lb = luminance(fg), luminance(bg)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def check(css: str | None = None):
    css = css if css is not None else design_tokens.SYSTEM_CSS.read_text(encoding="utf-8")
    rows = []
    for theme in design_tokens.THEMES:
        _, tok = design_tokens.computed(css, theme)
        col = {n: parse_color(v) for n, v in tok.items()
               if n.startswith("--c-") and not n.startswith("--c-mood")
               and n not in ("--c-scrim", "--c-overlay", "--c-wash-1", "--c-wash-2")}
        page = col["--c-page"]
        surface = over(col["--c-surface"], page)

        def add(kind, fg_name, fg, bg_name, bg, floor, enforced):
            r = ratio(over(fg, bg), bg)
            rows.append({"theme": theme, "kind": kind, "fg": fg_name, "bg": bg_name,
                         "ratio": round(r, 2), "floor": floor, "enforced": enforced,
                         "ok": r >= floor})

        for name in TEXT_ROLES:
            add("text", name, col[name], "--c-surface", surface, 4.5, True)
        add("text", "--c-on-accent", col["--c-on-accent"], "--c-accent",
            over(col["--c-accent"], surface), 4.5, True)
        for tone in STATUS_TONES:
            r, g, b, a = col[f"--c-{tone}"]
            fill = over((r, g, b, a * 0.22), surface)
            add("status-text", f"--c-{tone}-text", col[f"--c-{tone}-text"],
                f"--c-{tone} 22% on --c-surface", fill, 4.5, True)
            add("status-border", f"--c-{tone} 45%", (r, g, b, a * 0.45), "--c-surface", surface,
                3.0, False)
        add("focus", "--c-focus", col["--c-focus"], "--c-surface", surface, 3.0, True)
        add("focus", "--c-focus", col["--c-focus"], "--c-page", page, 3.0, True)
        for name in EDGES:
            add("edge", name, col[name], "--c-surface", surface, 3.0, False)
        for name in HP:
            add("hp", name, col[name], "--c-surface", surface, 3.0, False)
    return rows


def main(argv):
    rows = check()
    if "--json" in argv:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            mark = "ok" if r["ok"] else ("FAIL" if r["enforced"] else "below 3:1 (reported)")
            print(f"{r['theme']:<10} {r['kind']:<13} {r['fg']:<24} on {r['bg']:<32} "
                  f"{r['ratio']:>6.2f}  floor {r['floor']}  {mark}")
    failures = [r for r in rows if r["enforced"] and not r["ok"]]
    reported = [r for r in rows if not r["enforced"] and not r["ok"]]
    print(f"\n{len(rows)} pairs, {len(failures)} enforced failure(s), "
          f"{len(reported)} reported below 3:1")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
