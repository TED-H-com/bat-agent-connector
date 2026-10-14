"""Rebuild whole-file seconds from downloaded pytest JUnit XML (junit_family=xunit1)."""

from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path, PurePosixPath
from statistics import median


def rebuild(artifacts: Path) -> dict:
    samples: dict[str, list[float]] = defaultdict(list)
    reports = sorted(artifacts.rglob("junit.xml"))
    if not reports:
        raise ValueError("no junit.xml reports found")
    for report in reports:
        totals: dict[str, float] = defaultdict(float)
        # These are CI-produced pytest reports, not external provider XML.
        for case in ET.parse(report).iter("testcase"):  # noqa: S314
            name = case.get("file", "").replace("\\", "/")
            path = PurePosixPath(name)
            if not name.startswith("tests/") or path.suffix != ".py" or ".." in path.parts:
                raise ValueError(f"{report}: missing/invalid testcase file; use junit_family=xunit1")
            seconds = float(case.attrib["time"])
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError(f"{report}: invalid testcase duration")
            totals[name] += seconds
        for name, seconds in totals.items():
            samples[name].append(seconds)
    if not samples:
        raise ValueError("reports contain no testcases")
    # Each file runs once per Python version. Do not add the four versions together.
    return {"schema_version": 1, "basis": "junit-seconds-median", "files": {
        name: round(median(seconds), 6) for name, seconds in sorted(samples.items())
    }}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--output", type=Path, default=Path("tests/ci_durations.json"))
    args = parser.parse_args()
    try:
        data = rebuild(args.artifacts)
        args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, ET.ParseError) as exc:
        print(f"Durations refresh failed: {exc}")
        return 1
    print(f"Wrote median seconds for {len(data['files'])} files to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
