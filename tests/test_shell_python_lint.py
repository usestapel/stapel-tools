"""A shell script invoking Python can import a module that no longer exists.

``iron-auth/bootstrap.sh`` ran a heredoc beginning ``from common.django.openid
import ...`` — a module deleted at the stapel migration. It failed on every
boot for months and nobody knew, because the script did not stop on error.

The literal incident is the first test in this file. The rest pins the two
rules and, just as importantly, what they deliberately do NOT claim: a
third-party import is not examined, because "are this payload's imports
resolvable" is not statically decidable in general and a rule that pretended
otherwise would be noise.
"""
import stat

from stapel_tools.shell_python_lint import lint_tree, main


def _repo(tmp_path, files: dict[str, str]):
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if rel.endswith(".sh"):
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def _rules(violations):
    return sorted(v.rule for v in violations)


# ---------------------------------------------------------------------------
# the incident
# ---------------------------------------------------------------------------


def test_the_iron_auth_bootstrap_shape_is_caught(tmp_path):
    root = _repo(tmp_path, {
        "common/__init__.py": "",
        "common/django/__init__.py": "",
        # common/django/openid.py was deleted at the stapel migration
        "bootstrap.sh": (
            "#!/bin/sh\n"
            "require \"openid keys\" python manage.py shell <<'EOF'\n"
            "from common.django.openid import ensure_keys\n"
            "ensure_keys()\n"
            "EOF\n"
        ),
    })
    violations = lint_tree(root)
    assert [v.rule for v in violations] == ["SH001"]
    assert "common.django.openid" in violations[0].message
    assert violations[0].line == 3, "points at the import, not the invocation"


def test_the_same_import_after_the_module_is_restored_is_clean(tmp_path):
    root = _repo(tmp_path, {
        "common/__init__.py": "",
        "common/django/__init__.py": "",
        "common/django/openid.py": "def ensure_keys(): pass\n",
        "bootstrap.sh": (
            "#!/bin/sh\nset -e\n"
            "python manage.py shell <<'EOF'\n"
            "from common.django.openid import ensure_keys\n"
            "EOF\n"
        ),
    })
    assert lint_tree(root) == []


# ---------------------------------------------------------------------------
# SH001 — scope: first-party only
# ---------------------------------------------------------------------------


def test_a_third_party_import_is_not_examined(tmp_path):
    """Not decidable, so not claimed. ``requests`` may or may not be installed
    in the interpreter this script runs; the linter cannot know and must not
    guess."""
    root = _repo(tmp_path, {
        "bootstrap.sh": (
            "#!/bin/sh\nset -e\n"
            "python - <<'EOF'\n"
            "import requests\n"
            "from django.db import connection\n"
            "EOF\n"
        ),
    })
    assert lint_tree(root) == []


def test_a_first_party_package_with_a_missing_submodule_is_caught(tmp_path):
    root = _repo(tmp_path, {
        "svc/apps/__init__.py": "",
        "svc/bootstrap.sh": "#!/bin/sh\nset -e\npython -c 'import apps.seed'\n",
    })
    assert _rules(lint_tree(root)) == ["SH001"]


def test_a_top_level_package_is_indexed_too(tmp_path):
    """The repo root is an import root like any other directory — a payload
    run from the repo root imports what sits next to it."""
    root = _repo(tmp_path, {
        "common/__init__.py": "",
        "bootstrap.sh": "#!/bin/sh\nset -e\npython -c 'import common.gone'\n",
    })
    assert _rules(lint_tree(root)) == ["SH001"]


def test_a_resolvable_first_party_import_is_clean(tmp_path):
    root = _repo(tmp_path, {
        "svc/apps/__init__.py": "",
        "svc/apps/seed.py": "",
        "svc/bootstrap.sh": "#!/bin/sh\nset -e\npython -c 'import apps.seed'\n",
    })
    assert lint_tree(root) == []


def test_a_bare_module_next_to_the_script_resolves(tmp_path):
    root = _repo(tmp_path, {
        "svc/seed.py": "",
        "svc/bootstrap.sh": "#!/bin/sh\nset -e\npython -c 'import seed'\n",
    })
    assert lint_tree(root) == []


def test_a_relative_import_is_not_this_rule_s_business(tmp_path):
    """A heredoc has no package context, so a relative import's resolution is
    a question this rule cannot answer."""
    root = _repo(tmp_path, {
        "common/__init__.py": "",
        "bootstrap.sh": (
            "#!/bin/sh\nset -e\npython - <<'EOF'\nfrom .nowhere import x\nEOF\n"
        ),
    })
    assert lint_tree(root) == []


def test_a_payload_that_does_not_parse_is_left_alone(tmp_path):
    root = _repo(tmp_path, {
        "bootstrap.sh": (
            "#!/bin/sh\nset -e\npython - <<'EOF'\nthis is not python(\nEOF\n"
        ),
    })
    assert lint_tree(root) == []


def test_noqa_silences_the_import_finding(tmp_path):
    root = _repo(tmp_path, {
        "common/__init__.py": "",
        "bootstrap.sh": (
            "#!/bin/sh\nset -e\npython - <<'EOF'\n"
            "from common.gone import x  # noqa: SH001\nEOF\n"
        ),
    })
    assert lint_tree(root) == []


# ---------------------------------------------------------------------------
# SH002 — the half that made it invisible for months
# ---------------------------------------------------------------------------


def test_an_unguarded_invocation_is_reported(tmp_path):
    root = _repo(tmp_path, {
        "bootstrap.sh": (
            "#!/bin/sh\npython manage.py migrate\npython manage.py runserver\n"
        ),
    })
    assert _rules(lint_tree(root)) == ["SH002", "SH002"]


def test_set_e_satisfies_it(tmp_path):
    root = _repo(tmp_path, {
        "bootstrap.sh": "#!/bin/sh\nset -e\npython manage.py migrate\n",
    })
    assert lint_tree(root) == []


def test_the_per_step_verbs_satisfy_it(tmp_path):
    """A blanket ``set -e`` makes cosmetic steps fatal, which is its own
    defect — the generated boot contract classifies each step instead, and
    this rule must accept that discipline rather than push scripts back onto
    the flag."""
    root = _repo(tmp_path, {
        "bootstrap.sh": (
            "#!/bin/sh\n"
            ". /usr/local/lib/stapel-bootstrap.sh || exit 1\n"
            "require \"migrations\" python manage.py migrate\n"
            "optional \"static\" python manage.py collectstatic --noinput\n"
        ),
    })
    assert lint_tree(root) == []


def test_an_explicit_failure_handler_satisfies_it(tmp_path):
    root = _repo(tmp_path, {
        "bootstrap.sh": "#!/bin/sh\npython manage.py migrate || exit 1\n",
    })
    assert lint_tree(root) == []


def test_a_commented_out_invocation_is_not_a_finding(tmp_path):
    root = _repo(tmp_path, {"bootstrap.sh": "#!/bin/sh\n# python manage.py migrate\n"})
    assert lint_tree(root) == []


def test_a_whole_script_can_be_excluded(tmp_path):
    root = _repo(tmp_path, {
        "bootstrap.sh": "#!/bin/sh\n# stapel-lint: ignore\npython manage.py migrate\n",
    })
    assert lint_tree(root) == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_exits_nonzero_on_a_finding(tmp_path, capsys):
    _repo(tmp_path, {"bootstrap.sh": "#!/bin/sh\npython manage.py migrate\n"})
    assert main([str(tmp_path)]) == 1
    assert "SH002" in capsys.readouterr().err


def test_cli_exits_zero_when_clean(tmp_path):
    _repo(tmp_path, {"bootstrap.sh": "#!/bin/sh\nset -e\necho hello\n"})
    assert main([str(tmp_path)]) == 0


def test_cli_can_select_one_rule(tmp_path, capsys):
    _repo(tmp_path, {
        "common/__init__.py": "",
        "bootstrap.sh": "#!/bin/sh\npython -c 'import common.gone'\n",
    })
    assert main([str(tmp_path), "--rules", "SH001"]) == 1
    err = capsys.readouterr().err
    assert "SH001" in err
    assert "SH002" not in err
