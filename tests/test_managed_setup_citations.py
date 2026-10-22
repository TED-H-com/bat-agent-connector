"""The setup contract cites actual handlers, rather than stale line numbers."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("doc", ["api-v1.md", "fleet-configuration.md"])
def test_setup_preview_citations_point_to_handlers(doc):
    text = (ROOT / "docs/design" / doc).read_text()
    row = next(line for line in text.splitlines() if line.startswith("| `POST /api/v1/managed/setup/preview`")
               or line.startswith("| personal 安裝的 `setup.host`"))
    lines = (ROOT / "src/bat_agent_connector/managed_setup.py").read_text().splitlines()
    cited = [lines[int(number) - 1] for number in re.findall(r"managed_setup\.py#L(\d+)", row)]
    assert any(line.startswith("def _review_candidate(") for line in cited), cited
