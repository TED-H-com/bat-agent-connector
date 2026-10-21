"""Generate private candidate facts; no network, publishing or support wording."""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from scripts.release_capability_matrix import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, help="JSON list of exact-row observations")
    parser.add_argument("--claims", type=Path, help="JSON list of reviewed candidate table-cell interpretations")
    parser.add_argument("--producer-golden", type=Path, help="Write de-identified producer output for freshness tests")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="capability-matrix-") as scratch:
        result = generate(args.source, args.candidate, scratch=scratch,
                          receipts=json.loads(args.receipts.read_text()) if args.receipts else (),
                          claims=json.loads(args.claims.read_text()) if args.claims else ())
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    if args.producer_golden:
        args.producer_golden.write_text(json.dumps(result["producers"], indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"candidate": result["candidate"], "rows": len(result["rows"]),
                      "mismatches": len(result["consistency"]["mismatches"]),
                      "unchecked": len(result["consistency"]["unchecked"]),
                      "rejected_receipts": len(result["rejected_receipts"])}))
    return 1 if result["consistency"]["mismatches"] or result["rejected_receipts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
