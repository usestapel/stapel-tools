"""Тег без релиза — не релиз (stapel-registry-check, задача #232).

Фикстуры ниже — три РЕАЛЬНЫХ случая из этого флота, а не выдуманные:

* четыре либы несли теги версий, которых на PyPI никогда не было — publish
  падал на своей же конфигурации доверенного издателя (#48);
* 0.15.2 и 0.15.3 не уехали с машины вовсе: `--follow-tags` молча не пушит
  легковесный тег, а push отрапортовал успех, потому что жаловаться ему было
  не на что;
* 2026-08-06 — GitHub Actions в `major_outage`: коммиты и теги на удалёнке
  есть, воркфлоу не создан ни один, на PyPI не уехало ничего.

Все три обнаружены руками. Гейт стоит одного HTTP-запроса.

САМОЕ ВАЖНОЕ здесь — последний класс тестов: недоступный реестр НЕ считается
успехом. Гейт публикации, который зеленеет, когда не смог спросить, — ровно
тот дефект, против которого этот модуль и написан.
"""
import json
import subprocess

import pytest

from stapel_tools import registry_check as rc


@pytest.fixture
def repo(tmp_path):
    """Инициализированный git-репозиторий с pyproject."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "stapel-demo"\nversion = "0.3.0"\n', encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def tag(repo, *versions):
    """Проставить теги версий (пустыми коммитами — содержимое не важно)."""
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit",
         "-q", "--allow-empty", "-m", "x"],
        cwd=repo, check=True,
    )
    for version in versions:
        subprocess.run(["git", "tag", f"v{version}"], cwd=repo, check=True)


def published(monkeypatch, *versions, kind="pypi"):
    monkeypatch.setattr(rc, "released_versions", lambda name, k: set(versions))


def codes(findings):
    return [f.rule for f in findings]


# ---------------------------------------------------------------------------
# REG001 — тот, который поймал бы все три инцидента
# ---------------------------------------------------------------------------


class TestTagWithoutRelease:
    def test_tag_not_in_registry_is_an_error(self, repo, monkeypatch):
        tag(repo, "0.3.0")
        published(monkeypatch)  # реестр пуст — публикация не состоялась
        findings = rc.check_repo(repo)
        assert codes(findings) == ["REG001"]
        assert findings[0].level == "error"

    def test_message_names_the_version_and_the_registry(self, repo, monkeypatch):
        tag(repo, "0.15.2")
        published(monkeypatch, "0.15.1")
        (finding,) = [f for f in rc.check_repo(repo) if f.rule == "REG001"]
        assert "0.15.2" in finding.message and "PyPI" in finding.message

    def test_released_tag_is_silent(self, repo, monkeypatch):
        tag(repo, "0.3.0")
        published(monkeypatch, "0.3.0")
        assert rc.check_repo(repo) == []

    def test_every_dead_tag_is_named_separately(self, repo, monkeypatch):
        # Инцидент #48: четыре либы, а не одна. Свернуть их в «есть проблемы»
        # значило бы заставить человека выяснять список руками.
        tag(repo, "0.1.0", "0.2.0", "0.3.0")
        published(monkeypatch, "0.1.0")
        assert codes(rc.check_repo(repo)).count("REG001") == 2


class TestPrehistory:
    """Теги старше первого релиза — не сломанный релиз, а «пакета ещё не было».

    Замер по флоту 07.08.2026: 100 недостающих тегов, 60 из них — предыстория.
    Без этого отсечения настоящие сорок (в том числе застрявшая в тот день
    stapel-core 0.19.0) тонут в шуме, а гейт, который никто не читает, — это
    отсутствующий гейт.
    """

    def test_tags_before_the_first_release_are_not_errors(self, repo, monkeypatch):
        tag(repo, "0.1.0", "0.2.0", "0.3.0")
        published(monkeypatch, "0.3.0")  # первый релиз — 0.3.0
        assert rc.check_repo(repo) == []

    def test_but_the_count_is_always_said_out_loud(self, repo, monkeypatch):
        tag(repo, "0.1.0", "0.2.0", "0.3.0")
        published(monkeypatch, "0.3.0")
        notes = []
        rc.check_repo(repo, notes=notes)
        assert notes and "2 тег" in notes[0] and "--all-history" in notes[0]

    def test_a_hole_after_the_first_release_stays_an_error(self, repo, monkeypatch):
        # Ровно класс #48: ранние версии опубликованы, средняя — нет.
        tag(repo, "0.1.0", "0.2.0", "0.3.0")
        published(monkeypatch, "0.1.0", "0.3.0")
        (finding,) = rc.check_repo(repo)
        assert finding.rule == "REG001" and "0.2.0" in finding.message

    def test_all_history_brings_them_back(self, repo, monkeypatch):
        tag(repo, "0.1.0", "0.2.0", "0.3.0")
        published(monkeypatch, "0.3.0")
        assert len(rc.check_repo(repo, all_history=True)) == 2

    def test_nothing_published_at_all_keeps_every_tag(self, repo, monkeypatch):
        # Пустой реестр — не «предыстория», а полностью несостоявшийся выпуск:
        # отсекать тут не от чего, и молчать было бы худшим из исходов.
        tag(repo, "0.1.0", "0.2.0")
        published(monkeypatch)
        assert codes(rc.check_repo(repo)).count("REG001") == 2

    def test_version_key_orders_numerically_not_lexically(self):
        # '0.9.0' > '0.10.0' по строке — на этом отсечение съело бы настоящую дыру.
        assert rc.version_key("0.9.0") < rc.version_key("0.10.0")
        assert rc.version_key("1.0.0") > rc.version_key("0.99.99")

    def test_unparsable_version_does_not_crash_the_gate(self):
        assert isinstance(rc.version_key("0.1.0rc1"), tuple)
        assert isinstance(rc.version_key("weird"), tuple)


# ---------------------------------------------------------------------------
# REG002/REG003 — предупреждения, не блокеры
# ---------------------------------------------------------------------------


class TestWarnings:
    def test_release_without_tag_is_a_warning(self, repo, monkeypatch):
        tag(repo, "0.3.0")
        published(monkeypatch, "0.3.0", "0.2.9")
        (finding,) = [f for f in rc.check_repo(repo) if f.rule == "REG002"]
        assert finding.level == "warning" and "0.2.9" in finding.message

    def test_untagged_unreleased_manifest_version_is_stated_once(self, repo, monkeypatch):
        published(monkeypatch)  # ни тегов, ни релизов — обычная разработка
        assert codes(rc.check_repo(repo)) == ["REG003"]

    def test_registry_version_newer_than_local_is_not_a_rule(self, repo, monkeypatch):
        # Устаревший чекаут — не дефект релиза; ловить его здесь значило бы
        # краснеть у всех, кто не сделал fetch.
        tag(repo, "0.3.0")
        published(monkeypatch, "0.3.0", "0.4.0")
        assert codes(rc.check_repo(repo)) == ["REG002"]


# ---------------------------------------------------------------------------
# Недоступный реестр ≠ «ничего не опубликовано»
# ---------------------------------------------------------------------------


class TestUnreachable:
    def test_unreachable_registry_refuses_to_report_success(self, repo, monkeypatch):
        tag(repo, "0.3.0")

        def boom(name, kind):
            raise rc.RegistryUnreachable("нет сети")

        monkeypatch.setattr(rc, "released_versions", boom)
        with pytest.raises(rc.RegistryUnreachable):
            rc.check_repo(repo)

    def test_cli_exits_nonzero_when_it_could_not_ask(self, repo, monkeypatch, capsys):
        tag(repo, "0.3.0")
        monkeypatch.setattr(
            rc, "released_versions",
            lambda n, k: (_ for _ in ()).throw(rc.RegistryUnreachable("нет сети")),
        )
        assert rc.main([str(repo)]) == 1
        assert "Refusing to report success" in capsys.readouterr().err

    def test_offline_ok_passes_but_says_so_loudly(self, repo, monkeypatch, capsys):
        tag(repo, "0.3.0")
        monkeypatch.setattr(
            rc, "released_versions",
            lambda n, k: (_ for _ in ()).throw(rc.RegistryUnreachable("нет сети")),
        )
        assert rc.main([str(repo), "--offline-ok"]) == 0
        # Молчаливого зелёного быть не должно даже под явным опт-аутом.
        assert "cannot reach" in capsys.readouterr().err

    def test_404_means_no_releases_yet_not_unreachable(self, monkeypatch):
        import urllib.error

        def not_found(url, timeout=None):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        monkeypatch.setattr(rc.urllib.request, "urlopen", not_found)
        assert rc.released_versions("stapel-brand-new", "pypi") == set()

    def test_500_is_unreachable_not_empty(self, monkeypatch):
        import urllib.error

        def server_error(url, timeout=None):
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

        monkeypatch.setattr(rc.urllib.request, "urlopen", server_error)
        with pytest.raises(rc.RegistryUnreachable):
            rc.released_versions("stapel-core", "pypi")


# ---------------------------------------------------------------------------
# Непроверенное названо непроверенным
# ---------------------------------------------------------------------------


class TestNotSilent:
    def test_repo_without_a_manifest_is_reported_not_skipped(self, tmp_path):
        notes = []
        assert rc.check_repo(tmp_path, notes=notes) == []
        assert notes and "nothing publishable" in notes[0]

    def test_nothing_found_is_not_a_pass(self, tmp_path, capsys):
        assert rc.main([str(tmp_path)]) == 1
        assert "NOTHING WAS VERIFIED" in capsys.readouterr().err

    def test_strict_turns_warnings_into_failure(self, repo, monkeypatch):
        published(monkeypatch)
        assert rc.main([str(repo)]) == 0
        assert rc.main([str(repo), "--strict"]) == 1


# ---------------------------------------------------------------------------
# npm и обход флота
# ---------------------------------------------------------------------------


class TestNpmAndDiscovery:
    def test_npm_package_is_understood(self, tmp_path, monkeypatch):
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "@stapel/auth-react", "version": "0.12.2"}),
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        tag(tmp_path, "0.12.2")
        seen = {}

        def capture(name, kind):
            seen["kind"] = kind
            return set()

        monkeypatch.setattr(rc, "released_versions", capture)
        (finding,) = rc.check_repo(tmp_path)
        assert seen["kind"] == "npm"
        assert "npm" in finding.message

    def test_discovery_walks_stapel_dirs(self, tmp_path):
        for name in ("stapel-core", "stapel-auth", "not-ours"):
            package = tmp_path / name
            package.mkdir()
            (package / "pyproject.toml").write_text(
                f'[project]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8"
            )
        assert [p.name for p in rc.discover_repos(tmp_path)] == [
            "stapel-auth", "stapel-core"
        ]

    def test_a_package_target_checks_itself(self, repo):
        assert rc.discover_repos(repo) == [repo]


# ---------------------------------------------------------------------------
# машиночитаемый вывод — для крона
# ---------------------------------------------------------------------------


def test_json_output_carries_findings_and_what_was_checked(repo, monkeypatch, capsys):
    tag(repo, "0.3.0")
    published(monkeypatch)
    assert rc.main([str(repo), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["checked"] == [repo.name]
    assert payload["findings"][0]["rule"] == "REG001"
