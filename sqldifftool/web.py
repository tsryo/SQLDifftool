"""Local web app: JSON API plus the single-page UI."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .compare import Comparison, ComparisonStore
from .connection import ConnectionSpec, friendly_error, sql_server_drivers
from .normalize import CompareOptions
from .profiles import DEFAULT_PATH, ProfileStore
from .report import STATIC_DIR, build_report, report_filename


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


SCRIPT_FILES = "files"


def _load_side(spec: ConnectionSpec, files: list[tuple[str, bytes]] | None):
    if files is not None:
        from .scriptparser import parse_scripts

        return parse_scripts(files, spec.label)
    from .extractor import extract_snapshot  # imported lazily so demo mode needs no ODBC

    return extract_snapshot(spec)


def _load_both(left: ConnectionSpec, right: ConnectionSpec, files: dict[str, list | None]):
    specs = {"left": left, "right": right}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {side: pool.submit(_load_side, spec, files[side]) for side, spec in specs.items()}
        results, errors = {}, {}
        for side, fut in futures.items():
            try:
                results[side] = fut.result()
            except ValueError as exc:  # script files that cannot be used
                errors[side] = str(exc)
            except Exception as exc:  # report both sides' failures at once
                errors[side] = friendly_error(exc)
    if errors:
        summary = "; ".join(m if m.startswith(specs[s].label + ":") else f"{specs[s].label}: {m}"
                            for s, m in errors.items())
        raise ApiError(f"Could not read the database schema. {summary}", sideErrors=errors)
    left_snap, right_snap = results["left"], results["right"]
    if left_snap.source != right_snap.source:
        db_snap, file_snap = (left_snap, right_snap) if right_snap.source == "scripts" else (right_snap, left_snap)
        file_snap.warnings.append({"level": "info", "text": (
            f"{db_snap.label} was read from a database and {file_snap.label} from script files. Tables are "
            "scripted differently from each source, so expect formatting-only differences.")})
    return left_snap, right_snap


def _compare_request() -> tuple[dict, dict[str, list | None]]:
    """The compare body, and each side's uploaded script files (None for a database side)."""
    if request.mimetype == "multipart/form-data":
        try:
            body = json.loads(request.form.get("payload") or "{}")
        except json.JSONDecodeError as exc:
            raise ApiError("Invalid request") from exc
    else:
        body = request.get_json(silent=True) or {}
    files: dict[str, list | None] = {}
    for side in ("left", "right"):
        if (body.get(side) or {}).get("source") == SCRIPT_FILES:
            files[side] = [(f.filename or "", f.read()) for f in request.files.getlist(f"{side}_files")]
        else:
            files[side] = None
    return body, files


def create_app(demo: bool = False, profiles_path: Path = DEFAULT_PATH) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.json.sort_keys = False
    store = ComparisonStore()
    profiles = ProfileStore(profiles_path)
    demo_id = None
    if demo:
        from .demo import build_demo_snapshots

        demo_id = store.add(Comparison(*build_demo_snapshots())).id

    def get_comparison(cid: str) -> Comparison:
        cmp = store.get(cid)
        if cmp is None:
            raise ApiError("This comparison is no longer available. Run the comparison again.", 404)
        return cmp

    @app.errorhandler(Exception)
    def handle_error(exc: Exception):
        if isinstance(exc, HTTPException):
            return exc
        if isinstance(exc, ApiError):
            return jsonify({"error": str(exc), **exc.extra}), exc.status
        if isinstance(exc, ValueError):
            return jsonify({"error": str(exc)}), 400
        app.logger.exception("Request failed")
        return jsonify({"error": friendly_error(exc)}), 500

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/state")
    def state():
        return {"demo": demo, "demoCompareId": demo_id, "drivers": sql_server_drivers()}

    @app.post("/api/test-connection")
    def test_connection():
        from .extractor import probe

        spec = ConnectionSpec.from_dict(request.get_json(silent=True))
        if spec.auth != "connstr" and not spec.database:
            raise ApiError(f"{spec.label}: database is required")
        try:
            return probe(spec)
        except ValueError:
            raise
        except Exception as exc:
            raise ApiError(friendly_error(exc)) from exc

    @app.post("/api/databases")
    def databases():
        from .extractor import list_databases

        spec = ConnectionSpec.from_dict(request.get_json(silent=True))
        try:
            return {"databases": list_databases(spec)}
        except ValueError:
            raise
        except Exception as exc:
            raise ApiError(friendly_error(exc)) from exc

    @app.get("/api/profiles")
    def list_profiles():
        return {"profiles": profiles.load()}

    @app.post("/api/profiles")
    def save_profile():
        return {"profiles": profiles.save(request.get_json(silent=True) or {})}

    @app.delete("/api/profiles/<name>")
    def delete_profile(name: str):
        return {"profiles": profiles.delete(name)}

    @app.post("/api/compare")
    def compare():
        body, files = _compare_request()
        left = ConnectionSpec.from_dict(body.get("left"))
        right = ConnectionSpec.from_dict(body.get("right"))
        for side, spec in (("left", left), ("right", right)):
            if files[side] is not None:
                if not files[side]:
                    raise ApiError(f"{spec.label}: choose at least one .sql file",
                                   sideErrors={side: "Choose at least one .sql file."})
                continue
            spec.validate()
            if spec.auth != "connstr" and not spec.database:
                raise ApiError(f"{spec.label}: database is required")
        left_snap, right_snap = _load_both(left, right, files)
        cmp = Comparison(left_snap, right_snap, CompareOptions.from_dict(body.get("options")),
                         body.get("excludes") or [])
        return store.add(cmp).payload()

    @app.get("/api/compare/<cid>")
    def get_compare(cid: str):
        return get_comparison(cid).payload()

    @app.post("/api/compare/<cid>/options")
    def set_options(cid: str):
        cmp = get_comparison(cid)
        cmp.apply_options(CompareOptions.from_dict(request.get_json(silent=True)))
        return cmp.payload()

    @app.get("/api/compare/<cid>/object")
    def object_detail(cid: str):
        detail = get_comparison(cid).detail(request.args.get("key", ""))
        if detail is None:
            raise ApiError("Object not found in this comparison", 404)
        return detail

    @app.get("/api/compare/<cid>/report")
    def report(cid: str):
        cmp = get_comparison(cid)
        return Response(build_report(cmp), mimetype="text/html", headers={
            "Content-Disposition": f'attachment; filename="{report_filename(cmp)}"'})

    return app
