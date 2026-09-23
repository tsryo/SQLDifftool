"""Standalone HTML report: the same UI with CSS, JS and comparison data inlined."""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .compare import Comparison

STATIC_DIR = Path(__file__).parent / "static"


def _inline_json(data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _replace_once(page: str, old: str, new: str) -> str:
    if old not in page:
        raise RuntimeError(f"index.html is missing {old!r}; cannot build report")
    return page.replace(old, new, 1)


def report_filename(cmp: Comparison) -> str:
    def part(s: str) -> str:
        return re.sub(r"[^\w.-]+", "_", s).strip("_") or "db"
    stamp = cmp.created_at.replace(":", "").replace("-", "")
    return f"sqldiff_{part(cmp.left.database)}_{part(cmp.left.label)}_vs_{part(cmp.right.label)}_{stamp}.html"


def build_report(cmp: Comparison) -> str:
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    highlight = (STATIC_DIR / "sqlhighlight.js").read_text(encoding="utf-8")
    app = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    title = f"SQL diff · {cmp.left.label} vs {cmp.right.label} · {cmp.left.database}"
    page = _replace_once(page, "<title>SQL Difftool</title>", f"<title>{html.escape(title)}</title>")
    page = _replace_once(page, '<link rel="stylesheet" href="/static/app.css">', f"<style>\n{css}\n</style>")
    page = _replace_once(page, '<script src="/static/sqlhighlight.js"></script>', f"<script>\n{highlight}\n</script>")
    page = _replace_once(page, '<script src="/static/app.js"></script>',
                         f"<script>window.__REPORT__ = {_inline_json(cmp.report_payload())};</script>\n"
                         f"<script>\n{app}\n</script>")
    return page
