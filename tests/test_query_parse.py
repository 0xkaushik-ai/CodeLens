from backend.query import parse_query
from backend.text import fts_match_expression, split_identifier, index_terms


def test_filters_are_split_from_text():
    pq = parse_query('retry logic in:backend -in:tests lang:py kind:fn in:"my dir"')
    assert pq.text == "retry logic"
    assert pq.include_paths == ["backend", "my dir"]
    assert pq.exclude_paths == ["tests"]
    assert pq.languages == ["python"]
    assert pq.kinds == ["function"]


def test_incomplete_filters_are_ignored_and_unknown_keys_stay_text():
    pq = parse_query("token lang:pyth in: kind:")
    assert pq.text == "token" and not pq.has_filters
    assert parse_query("http://x 12:30 error:").text == "http://x 12:30 error:"


def test_ts_alias_covers_tsx():
    assert parse_query("lang:ts").languages == ["typescript", "tsx"]


def test_sql_escapes_like_wildcards():
    where, params = parse_query("in:my_dir%").sql()
    assert "ESCAPE" in where and params == ["%/my\\_dir\\%%"]


def test_split_identifier():
    assert split_identifier("parseHTTPConfig_v2") == ["parse", "http", "config", "v", "2"]


def test_index_terms_only_from_compounds():
    terms = index_terms("def getUserName(): return plain").split()
    assert terms == ["get", "user", "name"]


def test_fts_expression_is_always_literal():
    expr = fts_match_expression('c++ "NOT  where is parseConfig')
    assert expr == '"c" OR "not" OR "parseconfig" OR "parse" OR "config"'
    assert fts_match_expression("where is the") is None
    assert fts_match_expression("load conf", prefix_last=True).endswith('"conf"*')
