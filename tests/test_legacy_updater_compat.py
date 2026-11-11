import zipfile

from tests.fixtures.legacy_v0_2_12_package_files import package_files
from tests.package_build import _build


def test_current_wheel_matches_legacy_source_map(tmp_path):
    from pathlib import Path

    source = Path(__file__).resolve().parents[1]
    wheel = _build(source, tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        assert "bat_agent_connector/skill/build-info.json" in archive.namelist()
        assert "bat_agent_connector/build-info.json" not in archive.namelist()
        archive.extractall(tmp_path / "wheel")
    assert package_files(tmp_path / "wheel/bat_agent_connector") == package_files(
        source / "src/bat_agent_connector")
