import io
import json
import re

import pytest

from sqldifftool.web import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(demo=True, profiles_path=tmp_path / "profiles.json")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def cid(client):
    return client.get("/api/state").get_json()["demoCompareId"]


def test_index_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"SQL Difftool" in res.data


def test_compare_payload(client, cid):
    data = client.get(f"/api/compare/{cid}").get_json()
    assert data["left"]["label"] == "Left" and data["right"]["label"] == "Right"
    assert data["summary"]["different"] == 11
    assert {o["status"] for o in data["objects"]} == {"different", "only_left", "only_right", "identical"}
    compat = next(p for p in data["dbProperties"] if p["name"] == "Compatibility level")
    assert compat == {"name": "Compatibility level", "left": 160, "right": 150, "equal": False, "significant": True}


def test_object_detail(client, cid):
    res = client.get(f"/api/compare/{cid}/object", query_string={"key": "table|dbo|orders"})
    detail = res.get_json()
    assert detail["status"] == "different"
    changed = [r for r in detail["rows"] if r["t"] == "c"]
    assert any("rs" in r for r in changed)
    assert client.get(f"/api/compare/{cid}/object", query_string={"key": "missing"}).status_code == 404


def test_options_reclassify_without_database(client, cid):
    data = client.post(f"/api/compare/{cid}/options", json={"ignore_whitespace": False}).get_json()
    assert data["options"]["ignore_whitespace"] is False
    status = {o["key"]: o["status"] for o in data["objects"]}
    assert status["procedure|dbo|usp_updatecustomer"] == "different"


def test_unknown_comparison_is_404(client):
    res = client.get("/api/compare/doesnotexist")
    assert res.status_code == 404
    assert "no longer available" in res.get_json()["error"]


def test_report_is_standalone(client, cid):
    res = client.get(f"/api/compare/{cid}/report")
    assert res.status_code == 200
    assert "attachment" in res.headers["Content-Disposition"]
    page = res.data.decode("utf-8")
    assert "/static/" not in page
    match = re.search(r"window\.__REPORT__ = (.*?);</script>", page, re.S)
    data = json.loads(match.group(1).replace("<\\/", "</"))
    assert data["id"] == cid
    assert "procedure|dbo|usp_getorders" in data["details"]


def test_compare_validates_input(client):
    res = client.post("/api/compare", json={"left": {"label": "DEV"}, "right": {"label": "ACC"}})
    assert res.status_code == 400
    assert "server is required" in res.get_json()["error"]


def test_profiles_never_store_passwords(client, tmp_path):
    body = {
        "name": "Sales",
        "left": {"label": "DEV", "server": "dev01", "database": "Sales", "auth": "sql",
                 "username": "u", "password": "secret"},
        "right": {"label": "ACC", "auth": "connstr",
                  "connection_string": "Server=acc01;Database=Sales;UID=u;PWD={se;cret};Encrypt=yes"},
    }
    profiles = client.post("/api/profiles", json=body).get_json()["profiles"]
    assert profiles[0]["name"] == "Sales"
    stored = (tmp_path / "profiles.json").read_text()
    assert "secret" not in stored and "se;cret" not in stored
    assert "Encrypt=yes" in stored
    assert client.delete("/api/profiles/Sales").get_json()["profiles"] == []


def _upload(client, left_files, right_files, right_source="files"):
    payload = {"left": {"label": "DEV", "source": "files"}, "right": {"label": "ACC", "source": right_source},
               "options": {}, "excludes": []}
    data = {"payload": json.dumps(payload),
            "left_files": [(io.BytesIO(d), n) for n, d in left_files],
            "right_files": [(io.BytesIO(d), n) for n, d in right_files]}
    return client.post("/api/compare", data=data, content_type="multipart/form-data")


def test_compare_script_files(client):
    dev = [("DEV/dbo.T.sql", b"CREATE TABLE dbo.T (a int NOT NULL, b int NULL) ON [PRIMARY]\nGO\n"),
           ("DEV/dbo.P.sql", b"CREATE PROCEDURE dbo.P AS SELECT 1\nGO\n")]
    acc = [("ACC/all.sql", b"CREATE TABLE [dbo].[T] (a int NOT NULL)\nGO\nCREATE OR ALTER PROCEDURE dbo.P AS SELECT 1\n")]
    res = _upload(client, dev, acc)
    assert res.status_code == 200, res.get_json()
    data = res.get_json()
    assert data["left"]["server"] == "Script files" and data["left"]["database"] == "DEV"
    status = {o["key"]: o["status"] for o in data["objects"]}
    assert status == {"table|dbo|t": "different", "procedure|dbo|p": "identical"}
    assert client.get(f"/api/compare/{data['id']}/report").status_code == 200


def test_compare_script_files_errors(client):
    res = _upload(client, [("a.sql", b"CREATE TABLE t (a int)")], [])
    assert res.status_code == 400
    assert res.get_json()["sideErrors"] == {"right": "Choose at least one .sql file."}
    res = _upload(client, [("a.sql", b"CREATE TABLE t (a int)")], [("b.sql", b"SELECT 1")])
    assert res.status_code == 400
    assert "no CREATE statements" in res.get_json()["sideErrors"]["right"]


def test_mixed_sources_warn(client, monkeypatch):
    from sqldifftool import extractor
    from sqldifftool.model import Snapshot

    monkeypatch.setattr(extractor, "extract_snapshot", lambda spec: Snapshot(spec.label, "srv", "db"))
    payload = {"left": {"label": "DEV", "source": "files"},
               "right": {"label": "ACC", "source": "db", "server": "srv", "database": "db"}}
    res = client.post("/api/compare", data={"payload": json.dumps(payload),
                                            "left_files": [(io.BytesIO(b"CREATE TABLE t (a int)"), "a.sql")]},
                      content_type="multipart/form-data")
    assert res.status_code == 200, res.get_json()
    warnings = res.get_json()["warnings"]["left"]
    assert any("read from a database" in w["text"] for w in warnings)
