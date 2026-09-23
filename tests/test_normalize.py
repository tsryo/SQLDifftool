from sqldifftool.normalize import CompareOptions, comparison_key, line_keys, normalize_base, tokenize

WS_ONLY = CompareOptions(ignore_whitespace=True)
STRICT = CompareOptions(ignore_whitespace=False, ignore_system_names=False)


def kinds(text):
    return [k for k, _ in tokenize(text) if k != "ws"]


def test_comment_markers_inside_strings_are_not_comments():
    assert kinds("SELECT '--not /* a comment' AS x") == ["word", "str", "word", "word"]


def test_nested_block_comment_is_one_token():
    toks = tokenize("/* a /* nested */ still comment */SELECT")
    assert toks[0] == ("bcom", "/* a /* nested */ still comment */")
    assert toks[1] == ("word", "SELECT")


def test_escaped_quotes_and_brackets():
    assert tokenize("'it''s'")[0] == ("str", "'it''s'")
    assert tokenize("[odd]]name]")[0] == ("qid", "[odd]]name]")


def test_normalize_base_line_endings_trailing_space_and_blank_edges():
    assert normalize_base("\r\n\r\nSELECT 1   \r\nFROM t\t\r\n\r\n") == "SELECT 1\nFROM t"


def test_create_or_alter_becomes_create():
    assert normalize_base("CREATE OR ALTER PROCEDURE dbo.p AS SELECT 1") == "CREATE PROCEDURE dbo.p AS SELECT 1"
    assert normalize_base("-- header\ncreate  or\talter view v as select 1") == "-- header\ncreate view v as select 1"


def test_create_or_alter_only_at_the_start():
    text = "CREATE PROCEDURE p AS EXEC('CREATE OR ALTER VIEW v AS SELECT 1')"
    assert normalize_base(text) == text


def test_ignore_whitespace_collapses_layout_but_keeps_word_boundaries():
    a = "SELECT  a,b\nFROM   t\tWHERE x=1"
    b = "SELECT a, b FROM t WHERE x = 1"
    assert comparison_key(a, WS_ONLY) == comparison_key(b, WS_ONLY)
    assert comparison_key("SELECT a", WS_ONLY) != comparison_key("SELECTa", WS_ONLY)


def test_whitespace_inside_string_literals_still_matters():
    assert comparison_key("SELECT 'a  b'", WS_ONLY) != comparison_key("SELECT 'a b'", WS_ONLY)


def test_whitespace_differences_count_when_option_is_off():
    assert comparison_key("SELECT  1", STRICT) != comparison_key("SELECT 1", STRICT)


def test_ignore_case_leaves_string_literals_alone():
    opts = CompareOptions(ignore_case=True)
    assert comparison_key("SELECT X FROM [T] WHERE c = 'A'", opts) == comparison_key("select x from [t] where c = 'A'", opts)
    assert comparison_key("WHERE c = 'A'", opts) != comparison_key("WHERE c = 'a'", opts)


def test_ignore_comments():
    opts = CompareOptions(ignore_whitespace=False, ignore_comments=True)
    a = "SELECT 1 -- one\n/* block\ncomment */\nFROM t"
    b = "SELECT 1\nFROM t"
    assert comparison_key(a, opts) == comparison_key(b, opts)
    assert comparison_key(a, STRICT) != comparison_key(b, STRICT)


def test_line_keys_align_with_lines_even_with_multiline_tokens():
    text = "SELECT 'multi\nline' AS s /* c1\nc2\nc3 */\nFROM t"
    for opts in (WS_ONLY, STRICT, CompareOptions(ignore_comments=True, ignore_case=True)):
        assert len(line_keys(text, opts)) == len(text.split("\n"))


def test_line_keys_ignore_indentation():
    assert line_keys("\tSET a = 1", WS_ONLY) == line_keys("    SET a=1", WS_ONLY)
