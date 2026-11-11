"""Leading SQL comments have one parse before fixed-statement discovery."""

import pytest

from tests.fixed_sql import discover


@pytest.mark.parametrize(("sql", "kind"), [
    pytest.param("SELECT 1", "SELECT", id="plain"),
    pytest.param(" \t\r\nSELECT", "SELECT", id="whitespace"),
    pytest.param("-- c\nSELECT", "SELECT", id="line-comment"),
    pytest.param("-- SELECT", "", id="line-comment-eof"),
    pytest.param("/* a */ SELECT", "SELECT", id="block-comment"),
    pytest.param("/* a *//* b */INSERT", "INSERT", id="adjacent-blocks"),
    pytest.param("/* a */ -- b\n UPDATE", "UPDATE", id="mixed-comments"),
    pytest.param("/* x /* y */ DELETE", "DELETE", id="non-nesting-block"),
    pytest.param("/***/SELECT", "SELECT", id="extra-asterisk"),
    pytest.param("/* ** */ WITH", "WITH", id="asterisks-in-block"),
    pytest.param("/* SELECT", "", id="unterminated-block"),
    pytest.param("/*" + "*//*" * 10_000, "", id="pathological-comments"),
    pytest.param("/* a */ SELECT /* b */ INSERT", "SELECT", id="first-close-before-keyword"),
    pytest.param("/* closed */ / bogus */ SELECT", "", id="invalid-gap-after-first-close"),
    pytest.param("sElEcT(1)", "SELECT", id="case-and-punctuation"),
    pytest.param("\u2003\x1cSELECT", "SELECT", id="unicode-whitespace"),
    pytest.param("\ufeffSELECT", "", id="bom-is-not-whitespace"),
    pytest.param("-- c\rSELECT", "", id="line-comment-cr-is-not-lf"),
    pytest.param("-- c\u2028SELECT", "", id="line-comment-unicode-separator-is-not-lf"),
    pytest.param("SELECT2", "", id="digit-is-not-word-boundary"),
    pytest.param("SELECT_", "", id="underscore-is-not-word-boundary"),
    pytest.param("SELECTé", "", id="unicode-letter-is-not-word-boundary"),
    pytest.param("SELECT\u0301", "SELECT", id="combining-mark-is-word-boundary"),
])
def test_first_sql_keyword(sql, kind):
    statements, dynamic = discover(f"db.execute({sql!r})")
    assert [row.kind for row in statements] == ([kind] if kind else [])
    assert not dynamic
