"""ISO-8601 acceptance is the same on Python 3.10 and 3.14."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bat_agent_connector import (
    deployment,
    pm_governance,
    pm_release_sources,
    pm_required_ci,
    pm_roadmap,
    pm_sprints,
    product_preferences,
    summarize,
    usage_adapters,
)
from bat_agent_connector.iso_time import parse_date, parse_datetime
from bat_agent_connector.operations import OperationError
from bat_agent_connector.pm_github_sources import Incomplete

SHA = "a" * 40
# Forms whose acceptance differs between 3.10 and 3.14, plus the 3.10 baseline.
REJECTED = (
    "2026-10-11T24:00:00Z",
    "2026-10-11T24:00:00+00:00",
    "2026-10-11T24:00+00:00",
    "2026-10-11T24+00:00",
    "2026-10-11T24:00:00.000Z",
    "2026-10-11T24:00:00.000000Z",
    "20261011T090000Z",
    "20261011T240000Z",
    "2026-W41-1",
    "2026-W41-1T09:00:00Z",
    "2026-10-11T09:00:00.1+00:00",
    "2026-10-11T09:00:00.12+00:00",
    "2026-10-11T09:00:00.1234+00:00",
    "2026-10-11T09:00:00.1234567Z",
    "2026-10-11T09:00:00,123Z",
    "2026-10-11T09:00:00+0000",
    "2026-10-11T09:00:00+00",
    "2026-02-30T09:00:00Z",
)
ACCEPTED = {
    "2026-10-11T09:30:00Z": "2026-10-11T09:30:00+00:00",
    "2026-10-11T09:30:00+00:00": "2026-10-11T09:30:00+00:00",
    "2026-10-11T09:30:00-04:00": "2026-10-11T09:30:00-04:00",
    "2026-10-11T09:00:00.123Z": "2026-10-11T09:00:00.123000+00:00",
    "2026-10-11T09:00:00.123456+00:00": "2026-10-11T09:00:00.123456+00:00",
    "2026-10-11T09:00+00:00": "2026-10-11T09:00:00+00:00",
    "2026-10-11T09+00:00": "2026-10-11T09:00:00+00:00",
    "2026-10-11 09:00:00Z": "2026-10-11T09:00:00+00:00",
    "2026-10-11t09:00:00Z": "2026-10-11T09:00:00+00:00",
    "2026-10-11T09:00:00+00:00:00": "2026-10-11T09:00:00+00:00",
    "2026-10-11T09:00:00+05:30:00": "2026-10-11T09:00:00+05:30",
    "2026-10-11T09:00:00+23:59": "2026-10-11T09:00:00+23:59",
    "2028-02-29T09:30:00Z": "2028-02-29T09:30:00+00:00",
    "2026-10-11T23:59:59Z": "2026-10-11T23:59:59+00:00",
}


@pytest.mark.parametrize("value", REJECTED)
def test_parser_rejects_forms_that_depend_on_the_python_version(value):
    with pytest.raises(ValueError):
        parse_datetime(value)


@pytest.mark.parametrize(("value", "expected"), ACCEPTED.items())
def test_parser_keeps_forms_python_3_10_accepts(value, expected):
    assert parse_datetime(value).isoformat() == expected


def test_parser_keeps_a_naive_datetime_naive():
    parsed = parse_datetime("2026-10-11T09:00:00")
    assert parsed.tzinfo is None and parsed.isoformat() == "2026-10-11T09:00:00"
    assert parse_datetime("2026-10-11").isoformat() == "2026-10-11T00:00:00"


@pytest.mark.parametrize("value", ["2026-W41-1", "2026-W41", "20261011", "2026-10-11T00:00:00Z", "2026-02-30"])
def test_date_parser_stays_calendar_day_only(value):
    with pytest.raises(ValueError):
        parse_date(value)


def test_date_parser_accepts_a_real_calendar_day():
    assert parse_date("2028-02-29").isoformat() == "2028-02-29"
    assert parse_date("2026-10-11").isoformat() == "2026-10-11"


@pytest.mark.parametrize("value,message", [
    ("2026-10-11T24:00:00Z", "real calendar"),
    ("2026-10-11T24:00:00+00:00", "real calendar"),
    ("2026-02-30T09:00:00Z", "real calendar"),
    ("20261011T090000Z", "seconds and timezone"),
    ("2026-W41-1", "seconds and timezone"),
    ("2026-W41-1T09:00:00Z", "seconds and timezone"),
    ("2026-10-11T09:00:00.1234567Z", "seconds and timezone"),
    ("2026-10-11T09:00:00,123Z", "seconds and timezone"),
    ("2026-10-11T09:00:00+0000", "seconds and timezone"),
    ("2026-10-11T09:00:00+00:00:30", "seconds and timezone"),
    ("2026-10-11T09:00:00.1+00:00", "seconds and timezone"),
])
def test_review_time_rejects_version_skewed_input_with_the_same_messages(value, message):
    with pytest.raises(OperationError, match=message) as caught:
        pm_governance._review_time(value)
    assert caught.value.code == "INVALID_PARAMS"


@pytest.mark.parametrize("value", [None, "2028-02-29T09:30:00Z", "2026-10-11T09:30:00-04:00", "2026-10-11T23:59:59+00:00"])
def test_review_time_preserves_accepted_strings(value):
    assert pm_governance._review_time(value) == value


@pytest.mark.parametrize("value", [
    "2026-10-11T24:00:00Z", "2026-10-11T24:00:00+00:00", "20261011T090000Z", "2026-W41-1T09:00:00Z",
    "2026-10-11T09:00:00.1234567Z", "2026-10-11T09:00:00,123Z", "2026-10-11T09:00:00+0000",
    "2026-10-11T09:00:00+02:00", "2026-10-11T09:00:00+00:00:30", "2026-10-11T09:00:00", "2026-10-11",
])
def test_sprint_dates_reject_non_utc_and_version_skewed_forms(value):
    with pytest.raises(OperationError, match="explicit UTC ISO-8601") as caught:
        pm_sprints._date(value)
    assert caught.value.code == "INVALID_PARAMS"


@pytest.mark.parametrize("value", [
    "2026-10-11T00:00:00Z", "2026-10-11T00:00:00+00:00", "2026-10-11T09:00:00.123Z",
    "2026-10-11T09:00:00.123456+00:00", "2026-10-11T09:00+00:00", "2026-10-11 09:00:00Z",
    "2026-10-11T09:00:00+00:00:00",
])
def test_sprint_dates_keep_explicit_utc_forms(value):
    parsed = pm_sprints._date(value)
    assert parsed.utcoffset().total_seconds() == 0
    assert parsed.isoformat().endswith("+00:00")


def test_sprint_interval_stores_the_original_utc_instant():
    start, end = pm_sprints._interval("2026-10-11T00:00:00Z", "2026-10-18T00:00:00Z")
    assert (start, end) == ("2026-10-11T00:00:00Z", "2026-10-18T00:00:00Z")
    assert pm_sprints._date("2026-10-11T09:00:00.123Z").isoformat() == "2026-10-11T09:00:00.123000+00:00"


@pytest.mark.parametrize("value", ["2026-W41-1", "20261011", "2026-11-01T00:00:00Z", "2026-02-30"])
def test_roadmap_dates_stay_yyyy_mm_dd(value):
    with pytest.raises(OperationError, match="INVALID_PARAMS"):
        pm_roadmap._date(value)


def test_roadmap_date_is_stored_unchanged():
    assert pm_roadmap._date("2026-11-01") == "2026-11-01"
    assert pm_roadmap._date("2028-02-29") == "2028-02-29"
    assert pm_roadmap._date(None) is None


@pytest.mark.parametrize("value", REJECTED + ("2026-10-11", "2026-10-11T09:00:00"))
def test_usage_timestamp_rejects_naive_and_version_skewed_forms(value):
    with pytest.raises(usage_adapters.UsageInvalid, match="invalid_timestamp"):
        usage_adapters.timestamp(value)


@pytest.mark.parametrize(("value", "expected"), [
    ("2026-10-11T10:00:00Z", "2026-10-11T10:00:00.000+00:00"),
    ("2026-10-02T10:00:00.001Z", "2026-10-02T10:00:00.001+00:00"),
    ("2026-10-11T09:00:00+05:30", "2026-10-11T03:30:00.000+00:00"),
    ("2026-10-11T09:00:00.123456Z", "2026-10-11T09:00:00.123+00:00"),
])
def test_usage_timestamp_normalizes_accepted_instants_to_utc_milliseconds(value, expected):
    assert usage_adapters.timestamp(value) == expected


def test_release_timestamp_rejects_hour_24_and_keeps_github_zulu():
    with pytest.raises(OperationError, match="explicit publication timestamp"):
        pm_release_sources._release({"published_at": "2026-10-11T24:00:00Z"}, 1, "stable")
    for value in ("20261011T090000Z", "2026-W41-1T09:00:00Z", "2026-10-11T09:00:00"):
        with pytest.raises(OperationError, match="explicit publication timestamp"):
            pm_release_sources._release({"published_at": value}, 1, "stable")
    with pytest.raises(OperationError, match="Git reference"):
        pm_release_sources._release(
            {"published_at": "2026-10-11T09:00:00Z", "id": 1, "draft": False, "prerelease": False}, 1, "stable")


def _status(created):
    return {"id": 7, "context": "build", "state": "success", "created_at": created}


@pytest.mark.parametrize("created", ["2026-10-11T24:00:00Z", "20261011T090000Z", "2026-W41-1T09:00:00Z", "2026-10-11T09:00:00"])
def test_required_status_time_rejects_version_skewed_provider_stamps(created):
    with pytest.raises(Incomplete) as caught:
        pm_required_ci.project(_status(created), "required_statuses", {}, SHA)
    assert caught.value.code == "required_ci_status_order_unproven"


def test_required_status_time_keeps_an_explicit_github_instant():
    result = pm_required_ci.project(_status("2026-10-11T09:00:00Z"), "required_statuses", {}, SHA)
    expected = datetime(2026, 10, 11, 9, tzinfo=timezone.utc).timestamp()
    assert result["created_at"] == expected


def test_artifact_expiry_does_not_treat_hour_24_as_the_next_day():
    assert deployment.unexpired("2099-01-01T00:00:00Z") is True
    assert deployment.unexpired("2099-01-01T24:00:00Z") is False
    assert deployment.unexpired("20261011T090000Z") is False
    assert deployment.unexpired("2020-01-01T00:00:00Z") is False
    assert deployment.unexpired(None) is False


@pytest.mark.parametrize("value", ["2026-10-11T24:00:00Z", "20261011T090000Z", "2026-W41-1T09:00:00Z", "2026-10-11T09:00:00"])
def test_host_usage_epoch_ignores_version_skewed_timestamps(value):
    assert product_preferences._epoch(value) is None


def test_host_usage_epoch_keeps_numbers_and_aware_iso():
    assert product_preferences._epoch(1_700_000_000_000) == 1_700_000_000
    expected = datetime(2026, 10, 11, 9, tzinfo=timezone.utc).timestamp()
    assert product_preferences._epoch("2026-10-11T09:00:00Z") == expected
    assert product_preferences._epoch("2026-10-11T09:00:00.123Z") == expected + 0.123


@pytest.mark.parametrize("value", ["2026-10-11T24:00:00Z", "20261011T090000Z", "2026-W41-1"])
def test_summary_time_ignores_version_skewed_strings(value):
    assert summarize.ts_to_ms(value) is None


def test_summary_time_treats_a_naive_iso_string_as_utc():
    expected = int(datetime(2026, 10, 11, tzinfo=timezone.utc).timestamp() * 1000)
    assert summarize.ts_to_ms("2026-10-11T00:00:00") == expected
    assert summarize.ts_to_ms("2026-10-11T00:00:00Z") == expected
    assert summarize.ts_to_ms(expected) == expected
