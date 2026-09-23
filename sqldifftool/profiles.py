"""Saved DEV/ACC connection pairs. Passwords are never written to disk."""
from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_PATH = Path.home() / ".sqldifftool" / "profiles.json"
SIDE_FIELDS = ("label", "server", "database", "auth", "username", "connection_string",
               "encrypt", "trust_server_certificate")
_PASSWORD_IN_CONNSTR = re.compile(r"(?i)(^|;)\s*(pwd|password)\s*=\s*(\{(?:[^}]|\}\})*\}|[^;]*)")


def _clean_side(side: dict | None) -> dict:
    side = {k: v for k, v in (side or {}).items() if k in SIDE_FIELDS}
    if side.get("connection_string"):
        side["connection_string"] = _PASSWORD_IN_CONNSTR.sub(r"\1", side["connection_string"]).strip(";")
    return side


class ProfileStore:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)

    def load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _write(self, profiles: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(profiles, indent=2), encoding="utf-8")

    def save(self, profile: dict) -> list[dict]:
        name = (profile.get("name") or "").strip()
        if not name:
            raise ValueError("Profile name is required")
        clean = {
            "name": name,
            "left": _clean_side(profile.get("left")),
            "right": _clean_side(profile.get("right")),
            "options": profile.get("options") or {},
            "excludes": profile.get("excludes") or [],
        }
        profiles = [p for p in self.load() if p.get("name", "").lower() != name.lower()]
        profiles.append(clean)
        profiles.sort(key=lambda p: p["name"].lower())
        self._write(profiles)
        return profiles

    def delete(self, name: str) -> list[dict]:
        profiles = [p for p in self.load() if p.get("name", "").lower() != name.lower()]
        self._write(profiles)
        return profiles
