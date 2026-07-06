"""stapel-i18n-seed — export a catalog seed from stapel-translate builtin fixtures.

i18n-shipping.md §5: the first ru (and other-language) values for our shipped
content are NOT machine-translated — they are lifted from an already-curated
corpus, the ``stapel-translate`` builtin fixtures (``fixtures/builtin/<lang>.json``,
a flat ``{key: text}`` of 155 ``error.*`` + 100 ``notification.*`` keys × 20
languages). This one-shot script projects that corpus into a flat seed file for
one domain + language, which ``manage.py translate_catalogs --seed <file>``
then applies to a module's catalog (only keys in the module's registry are
used; the rest are ignored).

    stapel-i18n-seed --fixtures ../stapel-translate/fixtures/builtin \
        --domain errors --lang ru --out seed.errors.ru.json

Requirement 5 ("clients don't spend tokens") is met by *copying* the paid-for
corpus, not by re-running an LLM.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: domain → the key prefix that domain owns in the translate fixtures.
DOMAIN_PREFIX = {
    "errors": "error.",
    "notifications": "notification.",
}


def build_seed(fixtures_dir: Path, domain: str, lang: str) -> dict[str, str]:
    """Flat ``{key: text}`` for *domain* + *lang* from ``<fixtures_dir>/<lang>.json``."""
    prefix = DOMAIN_PREFIX.get(domain)
    if prefix is None:
        raise ValueError(
            f"unknown domain {domain!r} — known: {sorted(DOMAIN_PREFIX)}"
        )
    path = fixtures_dir / f"{lang}.json"
    if not path.is_file():
        raise FileNotFoundError(f"no fixture for {lang!r} at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"fixture {path} is not a JSON object")
    return {
        k: v for k, v in data.items()
        if isinstance(k, str) and k.startswith(prefix)
        and isinstance(v, str) and v
    }


def _dump(seed: dict[str, str]) -> str:
    """Byte-stable (matches stapel_core.i18n.dump_catalog)."""
    return json.dumps(
        {k: seed[k] for k in sorted(seed)},
        ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stapel-i18n-seed",
        description="Export a translate-catalog seed from stapel-translate "
                    "builtin fixtures (i18n-shipping.md §5).",
    )
    parser.add_argument(
        "--fixtures", required=True,
        help="Path to stapel-translate/fixtures/builtin (holds <lang>.json).",
    )
    parser.add_argument("--domain", required=True, choices=sorted(DOMAIN_PREFIX))
    parser.add_argument("--lang", required=True, help="Language code, e.g. ru.")
    parser.add_argument(
        "--out", default="",
        help="Output seed file (default: stdout).",
    )
    args = parser.parse_args(argv)

    try:
        seed = build_seed(Path(args.fixtures), args.domain, args.lang)
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"stapel-i18n-seed: {exc}", file=sys.stderr)
        return 2

    payload = _dump(seed)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"stapel-i18n-seed: {len(seed)} {args.domain}.{args.lang} key(s) "
              f"→ {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
