"""Package fixture inputs follow the real project's build declarations."""
from scripts.validate_connector_update import ROOT, copy_fixture_source


def test_fixture_includes_declared_custom_hook(tmp_path):
    copy_fixture_source(ROOT, tmp_path)
    assert (tmp_path / "hatch_build.py").read_bytes() == (ROOT / "hatch_build.py").read_bytes()
