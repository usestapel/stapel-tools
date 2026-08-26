"""``scripts/e2e_npm_pins.py`` — the e2e installs what npm actually has.

The 0.54.0 publish died in the e2e job on ``No matching version found for
@stapel/auth-react@^0.16.1``: the nav/version mirror is copied from the sibling
stapel-react checkout, that checkout had 0.16.1 in its tree, and the publish
had not happened. The registry is the only authority the e2e's ``npm install``
consults, so the pins it uses have to be resolved against it — and every
fallback has to be NAMED, or "the pair has not shipped yet" becomes a fact
nobody sees until a user's generated project fails to install.

The npm calls are injected here. A test that talks to the live registry is a
test whose verdict changes when someone publishes, which is the opposite of
what a suite is for; the live behaviour was verified by hand against
@stapel/auth-react (mirror 0.16.1, npm 0.16.0) on 2026-08-26.
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "e2e_npm_pins.py"


@pytest.fixture
def gate():
    spec = importlib.util.spec_from_file_location("e2e_npm_pins", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: What npm has. Modelled on the real registry at the time of the incident.
PUBLISHED = {
    "@stapel/auth-react": ["0.15.0", "0.15.1", "0.16.0"],
    "@stapel/notifications-react": ["0.9.0", "0.9.1"],
    "@stapel/core": ["0.16.0", "0.17.0"],
    "@stapel/tokens-antd": ["0.5.0"],
}


def fake_npm(published=None):
    """A stand-in for ``npm view`` with npm's own exit codes and output."""
    published = PUBLISHED if published is None else published
    calls = []

    def run(args):
        calls.append(args)
        _view, target, *_rest = args
        package, _, spec = target.rpartition("@") if target.count("@") > 1 \
            else (target, "", "")
        versions = published.get(package or target)
        if versions is None:
            return 1, ""  # E404: npm has never heard of this package
        if not spec:
            return 0, versions[-1]
        if spec.startswith("^"):
            floor = spec[1:]
            major_minor = ".".join(floor.split(".")[:2])
            matches = [
                v for v in versions
                if v == floor or (v.startswith(major_minor + ".") and v >= floor)
            ]
        else:
            matches = [v for v in versions if v == spec.lstrip("=")]
        if not matches:
            return 1, ""  # E404: no matching version
        return 0, "\n".join(f"{package}@{v} '{v}'" for v in matches)

    run.calls = calls
    return run


def _manifest(**deps):
    return {"name": "e2e-frontend", "dependencies": dict(deps)}


def test_a_mirror_ahead_of_npm_falls_back_to_the_published_version(gate):
    manifest = _manifest(**{"@stapel/auth-react": "^0.16.1"})
    rewrites, ahead, unknown = gate.resolve_pins(manifest, fake_npm())
    assert rewrites == {("dependencies", "@stapel/auth-react"): "^0.16.0"}
    assert ahead == [("@stapel/auth-react", "^0.16.1", "0.16.0")]
    assert unknown == []


def test_a_pin_that_resolves_is_left_alone(gate):
    """The mirror IS the pin; resolving against npm must not drift it."""
    manifest = _manifest(**{"@stapel/auth-react": "^0.16.0"})
    rewrites, ahead, unknown = gate.resolve_pins(manifest, fake_npm())
    assert (rewrites, ahead, unknown) == ({}, [], [])


def test_third_party_ranges_are_never_rewritten(gate):
    """A miss on react or vite is a real defect, not a publish-order fact."""
    manifest = _manifest(react="^99.0.0", vite="^6.0.0")
    rewrites, ahead, unknown = gate.resolve_pins(manifest, fake_npm())
    assert (rewrites, ahead, unknown) == ({}, [], [])


def test_a_package_npm_never_heard_of_is_reported_not_papered_over(gate):
    manifest = _manifest(**{"@stapel/ghost-react": "^1.0.0"})
    rewrites, ahead, unknown = gate.resolve_pins(manifest, fake_npm())
    assert rewrites == {}
    assert ahead == []
    assert unknown == ["@stapel/ghost-react"]


def test_dev_dependencies_are_checked_too(gate):
    manifest = {"devDependencies": {"@stapel/tokens-antd": "^0.6.0"}}
    rewrites, _ahead, _unknown = gate.resolve_pins(manifest, fake_npm())
    assert rewrites == {("devDependencies", "@stapel/tokens-antd"): "^0.5.0"}


def test_apply_rewrites_the_file_in_place(gate, tmp_path):
    path = tmp_path / "package.json"
    path.write_text(json.dumps(_manifest(**{
        "@stapel/auth-react": "^0.16.1",
        "@stapel/notifications-react": "^0.9.1",
        "react": "^19.1.0",
    }), indent=2))
    ahead, unknown = gate.apply_pins(path, fake_npm())
    written = json.loads(path.read_text())["dependencies"]
    assert written == {
        "@stapel/auth-react": "^0.16.0",
        "@stapel/notifications-react": "^0.9.1",
        "react": "^19.1.0",
    }
    assert [name for name, _, _ in ahead] == ["@stapel/auth-react"]
    assert unknown == []


def test_apply_leaves_the_file_untouched_when_everything_resolves(gate, tmp_path):
    path = tmp_path / "package.json"
    original = json.dumps(_manifest(**{"@stapel/auth-react": "^0.16.0"}), indent=2)
    path.write_text(original)
    assert gate.apply_pins(path, fake_npm()) == ([], [])
    assert path.read_text() == original


def test_the_listing_names_every_mirror_ahead(gate, capsys):
    gate.report([("@stapel/auth-react", "^0.16.1", "0.16.0")], [])
    out = capsys.readouterr().out
    assert "MIRRORS AHEAD OF NPM" in out
    assert "@stapel/auth-react: mirror ^0.16.1, npm has 0.16.0" in out


def test_the_listing_says_so_when_there_is_nothing_to_say(gate, capsys):
    gate.report([], [])
    assert "every mirrored pin resolves on npm" in capsys.readouterr().out


def test_cli_rewrites_and_exits_zero(gate, tmp_path, capsys):
    path = tmp_path / "package.json"
    path.write_text(json.dumps(_manifest(**{"@stapel/auth-react": "^0.16.1"})))
    gate._npm = fake_npm()
    assert gate.main([str(path)]) == 0
    assert json.loads(path.read_text())["dependencies"] == {
        "@stapel/auth-react": "^0.16.0"
    }


def test_cli_strict_fails_when_a_mirror_is_ahead(gate, tmp_path):
    """The release path: a pin ahead of npm means the publish ORDER is wrong,
    and the person tagging is the one who can fix it."""
    path = tmp_path / "package.json"
    path.write_text(json.dumps(_manifest(**{"@stapel/auth-react": "^0.16.1"})))
    gate._npm = fake_npm()
    assert gate.main([str(path), "--strict"]) == 1


def test_cli_check_mode_does_not_touch_the_file(gate, tmp_path):
    path = tmp_path / "package.json"
    original = json.dumps(_manifest(**{"@stapel/auth-react": "^0.16.1"}))
    path.write_text(original)
    gate._npm = fake_npm()
    assert gate.main([str(path), "--check"]) == 0
    assert path.read_text() == original


def test_cli_reports_a_missing_file(gate, tmp_path, capsys):
    assert gate.main([str(tmp_path / "nope.json")]) == 2


def test_cli_fails_on_a_package_npm_does_not_know(gate, tmp_path):
    path = tmp_path / "package.json"
    path.write_text(json.dumps(_manifest(**{"@stapel/ghost-react": "^1.0.0"})))
    gate._npm = fake_npm()
    assert gate.main([str(path)]) == 2


def test_the_e2e_job_actually_runs_it():
    """A gate wired into nothing is the failure mode this repo already had
    once, with scripts/check_nav_manifest_sync.py."""
    root = Path(__file__).resolve().parents[1]
    for workflow in ("ci.yml", "publish.yml"):
        text = (root / ".github" / "workflows" / workflow).read_text()
        assert "e2e_npm_pins.py" in text, workflow
        # ...and BEFORE the install it is supposed to fix.
        assert text.index("e2e_npm_pins.py") < text.index("npm install"), workflow
