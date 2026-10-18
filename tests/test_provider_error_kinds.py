"""Specific error facts survive broad HTTP envelopes without text inference."""
import pytest

from bat_agent_connector.pm_providers import classify_provider_error


@pytest.mark.parametrize('code,expected', [
    ('insufficient_quota', 'quota_error'), ('quota_exceeded', 'quota_error'),
    ('invalid_api_key', 'auth_error'), ('unauthorized', 'auth_error'),
    ('rate_limit_exceeded', 'rate_limited'), ('rate_limited', 'rate_limited'),
])
def test_specific_provider_code_survives_429(code, expected):
    assert classify_provider_error(status=429, code=code) == expected


@pytest.mark.parametrize('status,code,expected', [
    (503, None, None), (500, 'unknown', None),
    (None, 'Error: insufficient_quota', None), (None, 'overloaded; retry', None),
    (None, {'message': 'quota exceeded'}, None), (None, ['insufficient_quota'], None),
    ('429', None, None), (429.0, None, None), (429, -32000, 'rate_limited'),
    (401, None, 'auth_error'), (403, None, 'auth_error'),
])
def test_unknown_or_malformed_errors_do_not_invent_a_provider_outcome(status, code, expected):
    assert classify_provider_error(status=status, code=code) == expected
