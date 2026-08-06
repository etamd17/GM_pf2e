#!/usr/bin/env python3
"""CI guard: every Jinja template under templates/ must parse.

Run from the repo root: `python tools/check_templates.py`. Exits non-zero
and prints the offending file:line if any template has a syntax error.
"""
import sys
import pathlib
import jinja2

TEMPLATES = pathlib.Path("templates")


def main():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATES)))
    bad = []
    files = sorted(TEMPLATES.rglob("*.html"))
    for p in files:
        rel = p.relative_to(TEMPLATES)
        try:
            env.parse(p.read_text(encoding="utf-8"))
        except jinja2.TemplateSyntaxError as e:
            bad.append(f"{rel}:{e.lineno}: {e.message}")
    if bad:
        print("Template parse errors:")
        for b in bad:
            print("  " + b)
        return 1
    print(f"OK: all {len(files)} templates parse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
