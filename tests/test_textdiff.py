from sqldifftool.normalize import CompareOptions
from sqldifftool.textdiff import change_counts, diff_rows, inline_segments

OPTS = CompareOptions()


def test_equal_changed_and_one_sided_rows():
    left = "a\nb\nSELECT x FROM t\nd"
    right = "a\nSELECT x, y FROM t\nd\ne"
    rows = diff_rows(left, right, OPTS)
    assert [r["t"] for r in rows] == ["e", "l", "c", "e", "r"]
    assert rows[0] == {"t": "e", "l": 1, "r": 1, "lt": "a", "rt": "a"}
    assert (rows[2]["lt"], rows[2]["rt"]) == ("SELECT x FROM t", "SELECT x, y FROM t")
    assert rows[-1] == {"t": "r", "r": 4, "rt": "e"}


def test_changed_block_pairs_similar_lines_not_positions():
    left = "[DiscountPct] DECIMAL(5, 2) NULL,\n[Notes] NVARCHAR(MAX) NULL,"
    right = "[Notes] NVARCHAR(1000) NULL,"
    rows = diff_rows(left, right, OPTS)
    assert [(r["t"], r.get("l"), r.get("r")) for r in rows] == [("l", 1, None), ("c", 2, 1)]


def test_unmatched_lines_between_pairs_stay_side_by_side():
    rows = diff_rows("alpha one\nbeta two", "gamma three\ndelta four", OPTS)
    assert [(r["t"], r.get("l"), r.get("r")) for r in rows] == [("c", 1, 1), ("c", 2, 2)]


def test_inline_segments_mark_only_the_change():
    left, right = inline_segments("SELECT x FROM t", "SELECT x, y FROM t", OPTS)
    assert "".join(t for t, _ in left) == "SELECT x FROM t"
    assert "".join(t for t, _ in right) == "SELECT x, y FROM t"
    assert [t for t, changed in right if changed] == [", y"]
    assert not any(changed for _, changed in left)


def test_dissimilar_lines_get_no_inline_segments():
    assert inline_segments("BEGIN", "RETURN @Total * 42;", OPTS) is None


def test_whitespace_only_line_change_is_equal_when_ignored():
    rows = diff_rows("\tSET a = 1", "    SET a=1", OPTS)
    assert rows == [{"t": "e", "l": 1, "r": 1, "lt": "\tSET a = 1", "rt": "    SET a=1"}]
    strict = diff_rows("\tSET a = 1", "    SET a=1", CompareOptions(ignore_whitespace=False))
    assert strict[0]["t"] == "c"


def test_only_one_side():
    rows = diff_rows("x\ny", None, OPTS)
    assert rows == [{"t": "l", "l": 1, "lt": "x"}, {"t": "l", "l": 2, "lt": "y"}]


def test_change_counts():
    assert change_counts("a\nb\nc", "a\nB\nc\nd", OPTS) == (1, 2)
