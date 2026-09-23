"""Local web app: JSON API plus the single-page UI."""
from __future__ import annotations

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


def _extract_both(left: ConnectionSpec, right: ConnectionSpec):
    from .extractor import extract_snapshot  # imported lazily so demo mode needs no ODBC

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {"left": pool.submit(extract_snapshot, left), "right": pool.submit(extract_snapshot, right)}
        results, errors = {}, {}
        for side, fut in futures.items():
            try:
                results[side] = fut.result()
            except Exception as exc:  # report both sides' failures at once
                errors[side] = friendly_error(exc)
    if errors:
        labels = {"left": left.label, "right": right.label}
        summary = "; ".join(f"{labels[s]}: {m}" for s, m in errors.items())
        raise ApiError(f"Could not read the database schema. {summary}", sideErrors=errors)
    return results["left"], results["right"]


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
        body = request.get_json(silent=True) or {}
        left = ConnectionSpec.from_dict(body.get("left"))
        right = ConnectionSpec.from_dict(body.get("right"))
        for spec in (left, right):
            spec.validate()
            if spec.auth != "connstr" and not spec.database:
                raise ApiError(f"{spec.label}: database is required")
        left_snap, right_snap = _extract_both(left, right)
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
