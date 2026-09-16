"""The artifact under test must be the artifact you edited.

THE FAILURE THIS EXISTS FOR
---------------------------
In a shared development virtualenv, `stapel_profiles` was installed as a
regular (non-editable) copy at 0.20.2 while the repo sat at 0.20.4. Every test
run in that repo imported the installed copy. The suite was green, the
coverage was real, and it was measuring a month-old library — for an unknown
number of sessions, because nothing ever said so.

It surfaced only by accident: a new handler was added, the test importing it
failed with `ImportError: cannot import name`, and the reason turned out to be
that `stapel_profiles` had never been resolving to the working tree at all.
Had the change been to an EXISTING function rather than a new name, the import
would have succeeded and the test would have exercised the old body.

That is the whole class: **a run that reports on a different copy of the thing
than the one you changed.** Same shape as a Docker build reusing a cached pip
layer after requirements.txt moved, and as a generated artifact committed
stale against its own source.

WHAT THIS CHECKS
----------------
One comparison, at session start, before any test runs: the package this
repository *builds* must import from inside this repository.

Siblings are deliberately NOT checked. A repo under test legitimately reaches
its siblings through installed copies — that is what a released dependency is,
and asserting otherwise would make every suite demand a full editable
workspace. The rule is narrow on purpose: you are responsible for the artifact
you are editing.

WHAT IT CANNOT SEE
------------------
Three things, stated so nobody reads a green run as more than it is:

* **a stale SIBLING.** If you are editing `stapel-core` in one repo and
  testing `stapel-alerts` in another, this says nothing about which core the
  alerts suite imported. Only the package under test is asserted.
* **a stale copy inside a container image.** The pip layer cache is the same
  family, but the boundary is a build, not an import — an image built before
  requirements.txt moved will happily import "the right path" for the wrong
  version. Catching that one means asserting the INSTALLED VERSION against the
  pin after the build, which is a deploy-time check, not a test-time one.
* **a generated artifact that is stale against its input.** `docs/*.json`
  embedding a version that pyproject has since moved is not an import problem;
  it is what each repo's own `contract-check` drift gate is for.

This check is also unnecessary in CI, and knowing why matters: CI installs the
repository under test from the checkout, so the copy and the source are the
same thing by construction. The defect is a property of a long-lived shared
development environment, which is precisely where nobody is watching.

DISABLING
---------
``--no-freshness-check``, or ``STAPEL_SKIP_FRESHNESS=1`` in the environment.
Both exist for the case of testing an INSTALLED artifact on purpose (a release
rehearsal, a wheel smoke test). Neither is a fix for the failure above.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

#: Marker written on the config when the check has already run, so a nested
#: session (pytest invoked inside a test) does not re-report.
_DONE = "_stapel_freshness_reported"

ENV_SKIP = "STAPEL_SKIP_FRESHNESS"


def pytest_addoption(parser):
    group = parser.getgroup("stapel")
    group.addoption(
        "--no-freshness-check",
        action="store_true",
        default=False,
        help=(
            "Skip the assertion that this repo's own package imports from "
            "this repo. Only for deliberately testing an installed artifact."
        ),
    )


def project_package(root: Path) -> str | None:
    """The import name of the package this repository builds, or ``None``.

    Read from ``[project] name`` in pyproject.toml with dashes normalised —
    `stapel-profiles` builds `stapel_profiles`. Deliberately a text scan
    rather than a toml parse: this runs before anything else in the session,
    on every interpreter the fleet supports, and tomllib is 3.11+ only on the
    stdlib path while some runners still carry older tooling. The shape it
    reads is the one `stapel-new-library` emits and every repo has.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    in_project = False
    for raw in pyproject.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_project = line == "[project]"
            continue
        if not in_project or not line.startswith("name"):
            continue
        _, _, value = line.partition("=")
        name = value.strip().strip('"').strip("'")
        if name.startswith("stapel"):
            return name.replace("-", "_")
        return None
    return None


def resolve_package(package: str) -> Path | None:
    """Where *package* would import from, without importing it.

    ``find_spec`` is used rather than ``import_module`` because this runs
    before the suite has configured anything — importing a Django app at this
    point can raise ``ImproperlyConfigured`` and would turn a freshness check
    into a boot failure.
    """
    try:
        spec = importlib.util.find_spec(package)
    except Exception:
        return None
    if spec is None:
        return None
    origin = spec.origin
    if origin in (None, "namespace") and spec.submodule_search_locations:
        origin = next(iter(spec.submodule_search_locations), None)
    if not origin:
        return None
    return Path(origin).resolve()


def stale_report(root: Path, package: str, resolved: Path) -> str:
    """The message. It names the stale path AND the fresh one, on purpose."""
    return (
        f"\n"
        f"STALE PACKAGE — this suite is not testing this repository.\n"
        f"\n"
        f"  package under test : {package}\n"
        f"  imports from       : {resolved}\n"
        f"  this repository    : {root}\n"
        f"\n"
        f"The tests would run against that installed copy, not the code you\n"
        f"edited, and would pass or fail on its behaviour. A green run here\n"
        f"would mean nothing about this working tree.\n"
        f"\n"
        f"Fix it in the environment, not in the test:\n"
        f"\n"
        f"    pip install -e {root} --no-deps\n"
        f"\n"
        f"If you are testing an installed artifact deliberately (a release\n"
        f"rehearsal, a wheel smoke test), pass --no-freshness-check or set\n"
        f"{ENV_SKIP}=1. Neither is a fix for the case above.\n"
    )


def pytest_sessionstart(session):
    config = session.config
    if getattr(config, _DONE, False):
        return
    setattr(config, _DONE, True)

    if config.getoption("--no-freshness-check", default=False):
        return
    if os.environ.get(ENV_SKIP):
        return

    root = Path(config.rootpath).resolve()
    package = project_package(root)
    if package is None:
        # Not a stapel library checkout (a service repo, a scratch dir). There
        # is no "own package" to assert, and inventing one would be noise.
        return

    resolved = resolve_package(package)
    if resolved is None:
        # Not importable at all. That is the suite's own problem and it will
        # say so far more clearly than this check could.
        return

    if root in resolved.parents or resolved == root:
        return

    # pytest.exit, not SystemExit: raising out of a hook is reported as an
    # INTERNALERROR with a traceback of pytest's own plumbing, which buries
    # the message under noise that looks like a bug in the checker. This
    # aborts the session cleanly and prints the report as the reason.
    import pytest

    pytest.exit(stale_report(root, package, resolved), returncode=3)


__all__ = [
    "ENV_SKIP",
    "project_package",
    "resolve_package",
    "stale_report",
    "pytest_addoption",
    "pytest_sessionstart",
]
