"""The transported carrier stays readable, lossless and interpreter-independent."""
import ast
import hashlib
import io
import shlex
import tokenize

import pytest

from bat_agent_connector import pm_worktree

SAMPLES = [
    'def literal():\n    """Docstring retained.\n\n    # not a comment\n        spaces\n\tinside\n    """\n    return """value\n\n# literal\n    literal\n\tvalue\n"""\n',
    'values = (\n    1,\n      2,  # inline comment retained\n    # comment-only\n)\nvalue = 1 + \\\n    2\n',
    '@decorator(\n    "value",\n)\ndef nested(value):\n    if value:\n        return f"{value!r:>8}"\n    # comment-only\n    return f"""start\n\n    # literal\n    {value}\n\tend"""\n',
    'def escaped():\n    value = "first\\\n    # literal continuation"\n    return value, "\t", "    "\n',
    'if True:\n\t# comment-only\n\tvalue = "\tinside"\n\tif value:\n\t\tvalue = 2\n',
    'if True:\n   value = 1\n   if value:\n    value = 2\n',
    'value = f"""start {\n    {"key": 1}["key"]\n} end"""\n',
]

FALLBACK_SAMPLES = [
    ('if True:\n\f    # comment-only\n\f    value = 1  # type: ignore[assignment]\n',
     'if True:\n\n\f    value = 1  # type: ignore[assignment]\n'),
    ('if True:\n   # comment-only\n   value = 1\n   if value:\n    value = 2\n',
     'if True:\n\n   value = 1\n   if value:\n    value = 2\n'),
    ('if True:\n\t# comment-only\n\tvalue = "\tinside"  # noqa: F841\n',
     'if True:\n\n\tvalue = "\tinside"  # noqa: F841\n'),
]


def tokens_and_lines(source, ignored_lines):
    return [(token.type, token.string, token.start[0], token.end[0])
            for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type not in (tokenize.INDENT, tokenize.DEDENT)
            and token.start[0] not in ignored_lines]


def assert_lossless(source, normalized):
    original_lines = io.StringIO(source).readlines()
    normalized_lines = io.StringIO(normalized).readlines()
    assert len(original_lines) == len(normalized_lines)
    emptied = {i for i, (a, b) in enumerate(zip(original_lines, normalized_lines), 1)
               if a != b and a.lstrip(' \t\f').startswith('#') and not b.strip()}
    assert tokens_and_lines(normalized, emptied) == tokens_and_lines(source, emptied)
    assert syntax_and_lines(normalized) == syntax_and_lines(source)
    compile(normalized, '<pm-carrier>', 'exec')


def syntax_and_lines(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        # Only columns change with indentation. Keep lineno and end_lineno.
        for name in ('col_offset', 'end_col_offset'):
            if hasattr(node, name):
                delattr(node, name)
    return ast.dump(tree, include_attributes=True)


@pytest.mark.parametrize('source', SAMPLES)
def test_adversarial_source_preserves_syntax_literals_and_lines(source):
    normalized = pm_worktree._normalize_host_source(source)
    assert_lossless(source, normalized)
    assert normalized.count('\n') == source.count('\n')
    assert pm_worktree._normalize_host_source(normalized) == normalized


@pytest.mark.parametrize('source,expected', FALLBACK_SAMPLES)
def test_irregular_indentation_is_unchanged_except_comment_only_lines(source, expected):
    normalized = pm_worktree._normalize_host_source(source)
    assert normalized == expected
    assert_lossless(source, normalized)


def test_complete_helper_is_plain_source_with_original_syntax_and_lines():
    source = pm_worktree._host_source()
    plan = {'config': {'managed_root': '/synthetic', 'remote_url': 'https://example.invalid/o/r'}}
    command = pm_worktree.host_script('op_' + 'd' * 32, plan, 'read.pm-revision')
    argv = shlex.split(command)
    assert argv[:2] == ['python3', '-c'] and len(argv) == 4
    # The -c argument (argv[2], after the executable and -c) is the helper itself.
    assert argv[2] == pm_worktree._normalize_host_source(source)
    assert_lossless(source, argv[2])
    assert argv[2].count('\n') == source.count('\n')


def test_normalized_bytes_are_pinned_across_python_versions():
    # Both supported endpoint suites assert this same digest, covering the full
    # helper and every adversarial sample despite their different f-string tokens.
    values = [pm_worktree._host_source(), *SAMPLES]
    encoded = b'\0'.join(pm_worktree._normalize_host_source(value).encode() for value in values)
    assert hashlib.sha256(encoded).hexdigest() == '295b2f072b000ef01992c983d9ab5b20859487469ca0be92b29895a1652b8ccd'


def test_host_script_caches_only_exact_assembled_source(monkeypatch):
    normalize = pm_worktree._normalize_host_source
    normalize.cache_clear()
    plan = {'config': {'managed_root': '/synthetic', 'remote_url': 'https://example.invalid/o/r'}}
    source = pm_worktree._host_source()
    try:
        for value, misses in ((source, 1), (source, 1), (source + '\nCACHE_SENTINEL = 1\n', 2), (source, 3)):
            monkeypatch.setattr(pm_worktree, '_host_source', lambda: value)
            command = pm_worktree.host_script('op_' + 'd' * 32, plan, 'read.pm-revision')
            assert shlex.split(command)[2] == normalize.__wrapped__(value)
            assert normalize.cache_info().misses == misses
            assert normalize.cache_info().currsize == 1
        assert normalize.cache_info().hits == 1
    finally:
        normalize.cache_clear()


@pytest.mark.parametrize('source,error', [
    ('if True:\n\tvalue = 1\n        value = 2\n', TabError),
    ('if True:\n    value = 1\n  value = 2\n', IndentationError),
    ('value = (\n    1\n', SyntaxError),
])
def test_untokenizable_source_is_returned_unchanged_on_every_interpreter(source, error):
    assert pm_worktree._normalize_host_source(source) == source
    with pytest.raises(error):
        compile(source, '<invalid-carrier>', 'exec')
