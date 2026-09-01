"""``stapel-fixture-lint`` — gate an importer's catalogue/vocabulary fixtures.

    stapel-fixture-lint <dir>            # <dir>/{catalog,vocabularies}/*.json
    stapel-fixture-lint <dir>/vocabularies
    stapel-fixture-lint one-fixture.json
    stapel-fixture-lint <dir> --json

The rules and what each one is for are in :mod:`stapel_tools.catalog_fixtures.validate`.
Django-free by construction: nothing here imports Django, a settings module or
a database — a fixture is checked as a file, before anything tries to load it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .validate import Finding, validate_file, validate_tree


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stapel-fixture-lint",
        description="Check emitted catalogue and vocabulary fixtures against the "
                    "contract their loaders depend on.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", nargs="?", default=".",
                        help="fixture directory or a single fixture file (default: .)")
    parser.add_argument("--json", action="store_true", help="machine output")
    return parser


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = Path(args.target)
    notes: list[str] = []
    findings: list[Finding]
    if target.is_file():
        findings = validate_file(target)
    elif target.is_dir():
        findings = validate_tree(target, notes)
    else:
        print("Error: no such file or directory: %s" % target, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(
            {
                "ok": not findings,
                "errors": len(findings),
                "findings": [finding.to_dict() for finding in findings],
                "notes": notes,
            },
            indent=2, sort_keys=True, ensure_ascii=False,
        ))
    else:
        for note in notes:
            print(note, file=sys.stderr)
        for finding in findings:
            print(finding)
        if findings:
            print("\n%d fixture defect(s) found." % len(findings), file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
