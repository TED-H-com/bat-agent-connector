"""Contract checks for the stable external automation facade; no live providers."""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bat_agent_connector import automation, config, errors, lifecycle, registry, relay, service, triage

REEXPORTS = {
    "load_config": config.load_config,
    "BatError": errors.BatError,
    "QUOTA_PATTERNS": triage.QUOTA_PATTERNS,
    "session_cleanup": lifecycle.session_cleanup,
    "session_failover": lifecycle.session_failover,
    "session_set_permissions": lifecycle.session_set_permissions,
    "list_entries": registry.list_entries,
    "session_answer": service.session_answer,
    "session_read": service.session_read,
    "session_send": service.session_send,
    "sessions_list": service.sessions_list,
    "sessions_triage": triage.sessions_triage,
    "parse_status": relay.parse_status,
    "status_footer": relay.status_footer,
}


def test_exports_and_signatures():
    expected = set(REEXPORTS) | {"API_VERSION", "open_fleet", "jev_for"}
    assert set(automation.__all__) == expected
    assert len(automation.__all__) == len(expected)
    assert automation.API_VERSION == 1
    for name, internal in REEXPORTS.items():
        exported = getattr(automation, name)
        assert exported is internal
        if callable(internal):
            assert callable(exported)
        if inspect.isfunction(internal):
            assert inspect.signature(exported) == inspect.signature(internal)
    assert callable(automation.open_fleet)
    assert callable(automation.jev_for)
    assert list(inspect.signature(automation.open_fleet).parameters) == ["config"]
    assert inspect.signature(automation.open_fleet).parameters["config"].default is None
    assert list(inspect.signature(automation.jev_for).parameters) == ["fleet"]


@pytest.mark.parametrize("explicit_config", [False, True])
@pytest.mark.parametrize("raises", [False, True])
async def test_open_fleet_closes(monkeypatch, explicit_config, raises):
    cfg = object()
    fleet = SimpleNamespace(close=AsyncMock())
    constructor = Mock(return_value=fleet)
    loader = Mock(return_value=cfg)
    monkeypatch.setattr(automation, "_Fleet", constructor)
    monkeypatch.setattr(automation, "load_config", loader)

    async def run():
        async with automation.open_fleet(cfg if explicit_config else None) as opened:
            assert opened is fleet
            fleet.close.assert_not_awaited()
            if raises:
                raise RuntimeError("sweep failed")

    if raises:
        with pytest.raises(RuntimeError, match="sweep failed"):
            await run()
    else:
        await run()
    constructor.assert_called_once_with(cfg)
    if explicit_config:
        loader.assert_not_called()
    else:
        loader.assert_called_once_with()
    fleet.close.assert_awaited_once_with()


@pytest.mark.parametrize("enabled", [False, True])
def test_jev_for(monkeypatch, enabled):
    cfg = object()
    fleet = SimpleNamespace(config=SimpleNamespace(jev=cfg))
    jev = SimpleNamespace(enabled=enabled, ask=AsyncMock())
    constructor = Mock(return_value=jev)
    monkeypatch.setattr(automation, "_Jev", constructor)
    assert automation.jev_for(fleet) is (jev if enabled else None)
    constructor.assert_called_once_with(cfg)
