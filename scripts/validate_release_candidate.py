#!/usr/bin/env python3
"""Inspect local component receipts; no network, publishing, signing or activation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_identity import COMPONENTS, EvidenceError, _commit, receipt, validate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("receipt", help="bind a producer claim and the bytes it covers")
    collect.add_argument("component", choices=COMPONENTS)
    collect.add_argument("artifact", type=Path)
    check = commands.add_parser("validate", help="compare all required receipts with one local candidate")
    check.add_argument("--candidate", required=True)
    check.add_argument("--source", type=Path, required=True, help="local Git object store; candidate need not be checked out")
    check.add_argument("--receipt", type=Path, action="append", required=True)
    check.add_argument("--required", choices=COMPONENTS, action="append",
                       help="explicit partial scope; omitted means every release component, including unavailable ones")
    args = parser.parse_args(argv)
    try:
        if args.command == "receipt":
            result = receipt(args.component, args.artifact)
            code = 0 if _commit(result["commit"]) is not None else 1
            if code:
                result.update(status="unknown", reason="CLAIM_UNAVAILABLE")
        else:
            documents, errors = [], []
            for path in args.receipt:
                try:
                    documents.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    errors.append({"receipt": str(path), "reason": "RECEIPT_UNREADABLE"})
            result = validate(args.candidate, documents, source=args.source, required=args.required or COMPONENTS)
            if errors:
                result.update(one_candidate=False, input_errors=errors)
            code = 0 if result["one_candidate"] else 1
    except (OSError, ValueError) as exc:
        result = {"one_candidate": False, "status": "unknown",
                  "reason": exc.reason if isinstance(exc, EvidenceError) else "EVIDENCE_UNREADABLE"}
        if args.command == "receipt":
            result["component"] = args.component
        code = 1
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
