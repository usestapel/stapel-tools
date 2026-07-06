"""stapel-i18n-seed — project a curated corpus into a catalog seed."""
import json

import pytest

from stapel_tools.i18n_seed import build_seed, main


def _fixtures(tmp_path):
    d = tmp_path / "builtin"
    d.mkdir()
    (d / "ru.json").write_text(json.dumps({
        "error.404.not_found": "Ресурс не найден",
        "error.429.rate_limit": "Повторите через {retry_after_minutes} минут.",
        "notification.welcome": "Добро пожаловать",
        "misc.other": "прочее",
    }, ensure_ascii=False), encoding="utf-8")
    return d


def test_build_seed_filters_by_domain_prefix(tmp_path):
    d = _fixtures(tmp_path)
    errors = build_seed(d, "errors", "ru")
    assert set(errors) == {"error.404.not_found", "error.429.rate_limit"}
    notifs = build_seed(d, "notifications", "ru")
    assert set(notifs) == {"notification.welcome"}


def test_build_seed_rejects_unknown_domain(tmp_path):
    with pytest.raises(ValueError):
        build_seed(_fixtures(tmp_path), "flows", "ru")


def test_build_seed_missing_fixture(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_seed(_fixtures(tmp_path), "errors", "de")


def test_cli_writes_byte_stable_seed(tmp_path):
    d = _fixtures(tmp_path)
    out = tmp_path / "seed.json"
    rc = main(["--fixtures", str(d), "--domain", "errors", "--lang", "ru",
               "--out", str(out)])
    assert rc == 0
    text = out.read_text()
    assert text.endswith("\n")
    # sorted keys
    data = json.loads(text)
    assert list(data) == sorted(data)
    # re-run identical (byte-stable)
    main(["--fixtures", str(d), "--domain", "errors", "--lang", "ru", "--out", str(out)])
    assert out.read_text() == text
