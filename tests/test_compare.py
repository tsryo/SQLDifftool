import pytest

from sqldifftool.compare import Comparison
from sqldifftool.demo import build_demo_snapshots
from sqldifftool.model import Snapshot
from sqldifftool.normalize import CompareOptions


@pytest.fixture
def cmp():
    return Comparison(*build_demo_snapshots())


def status(cmp, key):
    return cmp.entries[key].status


def test_demo_statuses_with_default_options(cmp):
    assert status(cmp, "table|dbo|orders") == "different"
    assert status(cmp, "table|sales|invoices") == "only_left"
    assert status(cmp, "table|dbo|legacyimport") == "only_right"
    assert status(cmp, "view|dbo|vactivecustomers") == "identical"
    # Only system-generated constraint names differ.
    assert status(cmp, "table|dbo|customers") == "identical"
    assert status(cmp, "type|dbo|orderlinelist") == "identical"
    # Only whitespace / CREATE OR ALTER / line endings differ.
    assert status(cmp, "procedure|dbo|usp_updatecustomer") == "identical"
    # Comment-only and case-only differences count by default.
    assert status(cmp, "procedure|dbo|usp_archiveorders") == "different"
    assert status(cmp, "procedure|dbo|usp_getcustomer") == "different"
    assert status(cmp, "trigger|dbo|trg_orders_audit") == "different"
    assert cmp.summary() == {"different": 11, "only_left": 4, "only_right": 2, "identical": 11}


def test_options_flip_statuses(cmp):
    cmp.apply_options(CompareOptions(ignore_whitespace=False, ignore_system_names=False,
                                     ignore_case=True, ignore_comments=True))
    assert status(cmp, "procedure|dbo|usp_updatecustomer") == "different"
    assert status(cmp, "table|dbo|customers") == "different"
    assert status(cmp, "procedure|dbo|usp_archiveorders") == "identical"
    assert status(cmp, "procedure|dbo|usp_getcustomer") == "identical"


def test_excludes(cmp):
    left, right = build_demo_snapshots()
    c = Comparison(left, right, excludes=["staging.*", "usp_Old*"])
    assert "table|staging|customerload" not in c.entries
    assert "procedure|dbo|usp_oldreport" not in c.entries
    assert "table|dbo|orders" in c.entries


def test_entries_are_ordered_by_category_then_name(cmp):
    cats = [e.category for e in cmp.entries.values()]
    assert cats.index("Table") < cats.index("View") < cats.index("Procedure") < cats.index("Schema")


def test_detail_has_rows_and_texts(cmp):
    d = cmp.detail("procedure|dbo|usp_getorders")
    assert d["status"] == "different"
    assert d["leftText"].startswith("CREATE PROCEDURE [dbo].[usp_GetOrders]")
    assert any(r["t"] != "e" for r in d["rows"])
    assert cmp.detail("nope") is None


def test_report_payload_embeds_only_non_identical(cmp):
    data = cmp.report_payload()
    assert "table|dbo|orders" in data["details"]
    assert "view|dbo|vactivecustomers" not in data["details"]


def test_snapshot_json_roundtrip():
    left, _ = build_demo_snapshots()
    again = Snapshot.from_dict(left.to_dict())
    assert again == left
