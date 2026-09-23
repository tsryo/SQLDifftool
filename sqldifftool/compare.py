"""Compare two snapshots: match objects, classify their status, and diff them on demand."""
from __future__ import annotations

import fnmatch
import uuid
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime

from .model import CATEGORIES, DbObject, Snapshot
from .normalize import CompareOptions, comparison_key, normalize_base
from .scripter import script_object
from .textdiff import change_counts, diff_rows

DIFFERENT, ONLY_LEFT, ONLY_RIGHT, IDENTICAL = "different", "only_left", "only_right", "identical"
STATUSES = (DIFFERENT, ONLY_LEFT, ONLY_RIGHT, IDENTICAL)


@dataclass
class Entry:
    key: str
    category: str
    schema: str
    name: str
    parent: str
    status: str
    left: DbObject | None
    right: DbObject | None
    left_text: str | None
    right_text: str | None
    left_changed: int = 0
    right_changed: int = 0

    def summary(self) -> dict:
        return {"key": self.key, "category": self.category, "schema": self.schema, "name": self.name,
                "parent": self.parent, "status": self.status,
                "leftChanged": self.left_changed, "rightChanged": self.right_changed}


def _excluded(obj: DbObject, patterns: list[str]) -> bool:
    candidates = [obj.display_name.lower(), obj.name.lower()]
    return any(fnmatch.fnmatchcase(c, p.lower()) for p in patterns for c in candidates)


def _side_meta(s: Snapshot) -> dict:
    return {"label": s.label, "server": s.server, "database": s.database, "version": s.server_version,
            "edition": s.edition, "extractedAt": s.extracted_at, "objectCount": len(s.objects)}


class Comparison:
    def __init__(self, left: Snapshot, right: Snapshot, options: CompareOptions | None = None,
                 excludes: list[str] | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.left, self.right = left, right
        self.excludes = [p.strip() for p in (excludes or []) if p and p.strip()]
        self.options = CompareOptions()
        self.entries: dict[str, Entry] = {}
        self.apply_options(options or CompareOptions())

    def apply_options(self, options: CompareOptions) -> None:
        self.options = options
        left = {o.key: o for o in self.left.objects if not _excluded(o, self.excludes)}
        right = {o.key: o for o in self.right.objects if not _excluded(o, self.excludes)}
        entries = []
        for key in left.keys() | right.keys():
            entries.append(self._entry(key, left.get(key), right.get(key)))
        order = {c: i for i, c in enumerate(CATEGORIES)}
        entries.sort(key=lambda e: (order.get(e.category, 99), e.schema.lower(), e.name.lower()))
        self.entries = OrderedDict((e.key, e) for e in entries)

    def _script(self, obj: DbObject | None, snap: Snapshot) -> str | None:
        if obj is None:
            return None
        return normalize_base(script_object(obj, snap.collation, self.options.ignore_system_names))

    def _entry(self, key: str, l: DbObject | None, r: DbObject | None) -> Entry:
        lt, rt = self._script(l, self.left), self._script(r, self.right)
        obj = l or r
        e = Entry(key, obj.category, obj.schema, obj.name, obj.parent, IDENTICAL, l, r, lt, rt)
        if r is None:
            e.status, e.left_changed = ONLY_LEFT, lt.count("\n") + 1
        elif l is None:
            e.status, e.right_changed = ONLY_RIGHT, rt.count("\n") + 1
        elif comparison_key(lt, self.options) != comparison_key(rt, self.options):
            e.status = DIFFERENT
            e.left_changed, e.right_changed = change_counts(lt, rt, self.options)
        return e

    # --- payloads ----------------------------------------------------------

    def summary(self) -> dict:
        counts = Counter(e.status for e in self.entries.values())
        return {s: counts.get(s, 0) for s in STATUSES}

    def db_properties(self) -> list[dict]:
        l, r = self.left, self.right
        props = [("Collation", l.collation, r.collation, True),
                 ("Compatibility level", l.compat_level, r.compat_level, True),
                 ("Server version", l.server_version, r.server_version, False),
                 ("Edition", l.edition, r.edition, False)]
        return [{"name": n, "left": lv, "right": rv, "equal": lv == rv, "significant": sig}
                for n, lv, rv, sig in props]

    def payload(self) -> dict:
        return {
            "id": self.id,
            "createdAt": self.created_at,
            "left": _side_meta(self.left),
            "right": _side_meta(self.right),
            "options": self.options.to_dict(),
            "excludes": self.excludes,
            "summary": self.summary(),
            "dbProperties": self.db_properties(),
            "warnings": {"left": self.left.warnings, "right": self.right.warnings},
            "objects": [e.summary() for e in self.entries.values()],
        }

    def detail(self, key: str) -> dict | None:
        e = self.entries.get(key)
        if e is None:
            return None
        out = e.summary()
        out.update({
            "leftText": e.left_text,
            "rightText": e.right_text,
            "leftNote": e.left.note if e.left else "",
            "rightNote": e.right.note if e.right else "",
            "rows": diff_rows(e.left_text, e.right_text, self.options),
        })
        return out

    def report_payload(self) -> dict:
        data = self.payload()
        data["details"] = {k: self.detail(k) for k, e in self.entries.items() if e.status != IDENTICAL}
        return data


class ComparisonStore:
    """Keeps the most recent comparisons in memory."""

    def __init__(self, capacity: int = 10):
        self.capacity = capacity
        self._items: OrderedDict[str, Comparison] = OrderedDict()

    def add(self, cmp: Comparison) -> Comparison:
        self._items[cmp.id] = cmp
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)
        return cmp

    def get(self, cid: str) -> Comparison | None:
        return self._items.get(cid)
