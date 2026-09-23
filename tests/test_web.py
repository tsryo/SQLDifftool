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
    assert data["left"]["label"] == "DEV" and data["right"]["label"] == "ACC"
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
