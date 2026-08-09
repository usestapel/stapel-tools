"""SWAP004 — a vendor SDK imported outside the fleet library that owns it.

The rule exists because of a shape, not a package. A product carried its own
copy of a LiveKit provider next to the library's. It was not a bad copy — it
was AHEAD of the library on two capabilities, which is exactly how a provider
layer gets forked: never as a fork, always as one call the library did not
have yet, added where the engineer was standing. Then the library fixed
something real, and the product with the fork could not receive the fix at
all.

So the rule has to fire on the FIRST import in product code, and it has to
stay silent inside the owning library — otherwise it bans the very place the
capability is supposed to land. Both directions are asserted here for the same
reason SWAP003's tests do: a rule that only fires, or only stays quiet, is a
rule that gets suppressed wholesale on its first false hit.
"""
from pathlib import Path

from stapel_tools.swap_lint import find_swap004, lint_project

PYPROJECT = """\
[project]
name = "{name}"
version = "0.1.0"
dependencies = [{deps}]
"""


def build(tmp_path, files, *, name="meettoday", package="rooms", deps=()):
    root = tmp_path / f"proj{len(list(tmp_path.iterdir()))}"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        PYPROJECT.format(name=name, deps=", ".join(f'"{d}"' for d in deps)),
        encoding="utf-8",
    )
    pkg = root / package
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for rel, body in files.items():
        target = pkg / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


def codes(violations):
    return [(Path(v.path).name, v.rule) for v in violations]


# ── fires in product code ───────────────────────────────────────────────────


def test_a_from_import_of_the_vendor_sdk_is_an_error(tmp_path):
    root = build(tmp_path, {
        "providers.py": "from livekit.api import AccessToken, VideoGrants\n",
    })
    violations = find_swap004(root)
    assert codes(violations) == [("providers.py", "SWAP004")]
    assert "stapel_video" in violations[0].message


def test_a_plain_import_is_an_error_too(tmp_path):
    root = build(tmp_path, {"service.py": "import livekit.api\n"})
    assert codes(find_swap004(root)) == [("service.py", "SWAP004")]


def test_a_lazy_import_inside_a_function_is_still_an_error(tmp_path):
    # Where the fork actually grows: one call, deferred, inside a helper.
    root = build(tmp_path, {
        "service.py": "def kick(room, user):\n    from livekit import api\n    return api\n",
    })
    assert codes(find_swap004(root)) == [("service.py", "SWAP004")]


def test_every_offending_import_is_reported_not_just_the_first(tmp_path):
    root = build(tmp_path, {
        "a.py": "from livekit.api import AccessToken\n",
        "b.py": "import livekit\n",
    })
    assert sorted(codes(find_swap004(root))) == [
        ("a.py", "SWAP004"), ("b.py", "SWAP004"),
    ]


def test_it_composes_into_lint_project(tmp_path):
    root = build(tmp_path, {"providers.py": "from livekit.api import AccessToken\n"})
    assert "SWAP004" in {v.rule for v in lint_project(root)}


# ── silent where it must be ─────────────────────────────────────────────────


def test_the_owning_library_may_import_its_own_vendor(tmp_path):
    # The whole point of the ban is that the capability lands HERE.
    root = build(
        tmp_path,
        {"providers/livekit.py": "from livekit import api\n"},
        name="stapel-video",
        package="stapel_video",
    )
    assert find_swap004(root) == []


def test_a_package_merely_named_like_the_vendor_is_not_the_vendor(tmp_path):
    root = build(tmp_path, {"service.py": "from livekit_helpers import thing\n"})
    assert find_swap004(root) == []


def test_a_relative_import_is_never_the_vendor(tmp_path):
    root = build(tmp_path, {"service.py": "from .livekit import token\n"})
    assert find_swap004(root) == []


def test_an_unrelated_third_party_sdk_is_not_this_rules_business(tmp_path):
    # Not a dependency police: only vendors a fleet library owns the seam for.
    root = build(tmp_path, {"service.py": "import requests\nimport boto3\n"})
    assert find_swap004(root) == []


def test_tests_are_out_of_scope(tmp_path):
    # Fixtures legitimately fake a vendor; same posture as the other rules.
    root = build(tmp_path, {"tests/test_video.py": "from livekit.api import AccessToken\n"})
    assert find_swap004(root) == []


def test_a_deliberate_exception_can_be_suppressed(tmp_path):
    root = build(tmp_path, {
        "service.py": "from livekit.api import AccessToken  # noqa: SWAP004\n",
    })
    assert find_swap004(root) == []


def test_a_bare_noqa_suppresses_it(tmp_path):
    root = build(tmp_path, {"service.py": "import livekit  # noqa\n"})
    assert find_swap004(root) == []


def test_a_noqa_for_a_different_rule_does_not_suppress_it(tmp_path):
    root = build(tmp_path, {"service.py": "import livekit  # noqa: SWAP003\n"})
    assert codes(find_swap004(root)) == [("service.py", "SWAP004")]
