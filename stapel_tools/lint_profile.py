"""Per-project lint profile — the switch a legacy project needs to be gated
at all.

The problem this closes
-----------------------
``stapel-verify`` composes fourteen linters that encode *this fleet's*
contracts: ``StapelResponse`` discipline, presenter indirection, the
config-in-one-place law, the search-index contract, the SPA cache canon.
Against a project that stapel generated, every one of them is a fair gate.

Against an **imported legacy project** none of them is. A ten-year-old Django
shop that never heard of ``stapel_core`` trips R-codes, SWAP-codes and
CFG-codes on its first commit, in the hundreds, none of which describe a
defect — they describe the project not being a stapel project. A pipeline
that runs the arsenal there produces one thing only: a permanently red gate
the operator learns to ignore, which is worse than no gate.

The two honest answers, and the third that is not
--------------------------------------------------
1. **Turn the stapel checkers off for that surface** and say why. The
   project is then ungated on that surface, on the record.
2. **Point the gate at the project's OWN linter** — its ``ruff``, its
   ``eslint``, its ``golangci-lint``. The project keeps the standard it
   already has, and Studio's red-loop keeps working, because the loop only
   ever needed *a* verdict, never specifically stapel's.
3. Not an answer: leaving the arsenal on and teaching the humans to skim
   past it. A gate nobody reads is a gate that is off, without the record.

So the profile is a project-root file, ``stapel-lint.toml``, declaring a
**mode per surface**:

.. code-block:: toml

    # stapel-lint.toml
    [surface.python]
    mode = "native"
    command = "ruff check ."

    [surface.frontend]
    mode = "native"
    command = "npm run lint"

    [surface.docs]
    mode = "off"
    reason = "reference docs live in Confluence, not the repo"

    [waivers]
    SWAP002 = "presenters are the app's own; see ADR-7"

Three rules keep it from becoming a silent kill switch, all borrowed from the
fleet's waiver canon (``stapel_core.django.check_guard``):

* ``off`` **requires** a written ``reason``. An empty one is an error in the
  profile itself, not a quiet pass — the reason is the whole point.
* ``native`` **requires** a ``command``. "Use the project's own linter"
  without naming it is the same silence with extra steps.
* every non-``stapel`` surface still emits a report line carrying its mode
  and its reason, so ``stapel-verify --json`` — and Studio's rendering of it
  — shows what was **not** checked, next to what was.

Absent file ⇒ every surface is ``stapel``. A generated project needs no
profile and gets the full arsenal, unchanged.

Surfaces, not linters
---------------------
The unit is a surface (``python``, ``frontend``, ``docs``, ``i18n``,
``deploy``), not an individual linter, because that is the unit an operator
can actually reason about: "this project's Python is gated by its own ruff"
is a decision; "ADO002 is off but ADO003 is on" is a configuration accident
waiting to happen. Individual rules are still reachable — through
``[waivers]``, one id at a time, each with its reason, exactly like
``STAPEL_SECURITY_CHECK_WAIVERS``.
"""
from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

PROFILE_FILENAME = "stapel-lint.toml"

MODE_STAPEL = "stapel"
MODE_NATIVE = "native"
MODE_OFF = "off"
MODES = (MODE_STAPEL, MODE_NATIVE, MODE_OFF)

#: surface -> one-line description, used by the ``--explain`` output and by
#: the generated file's comments. The keys ARE the accepted surface names.
SURFACES: dict[str, str] = {
    "python": "backend Python: response/flow discipline, adoption, config, "
              "migrations, swap indirection, urls, index, authentication/"
              "authorization seams, HTTP surface versioning and surface "
              "contracts",
    "frontend": "the SPA: delivery canon (build output, entry document, asset "
                "hashing)",
    "docs": "in-repo documentation: model field docs",
    "i18n": "gettext catalogues under locale/",
    "deploy": "deployment-class files: nginx cache canon, environment "
              "addresses, private-name exposure",
}

#: Which surface each composed linter belongs to. A linter absent from this
#: map would silently escape every profile, so ``stapel_tools.verify`` asserts
#: the two lists agree.
LINTER_SURFACES: dict[str, str] = {
    "stapel-lint": "python",
    "stapel-adoption-lint": "python",
    "stapel-url-lint": "python",
    "stapel-authz-lint": "python",
    "stapel-config-lint": "python",
    "stapel-migration-lint": "python",
    "stapel-swap-lint": "python",
    "stapel-surface-lint": "python",
    "stapel-index-lint": "python",
    "stapel-api-lint": "python",
    "stapel-doc-lint": "docs",
    "stapel-nginx-cache-lint": "deploy",
    "stapel-env-address-lint": "deploy",
    "stapel-exposure-lint": "deploy",
    "stapel-frontend-delivery-lint": "frontend",
    "stapel-po-lint": "i18n",
}


class LintProfileError(Exception):
    """The profile file exists but does not say something it must say.

    Raised, never swallowed: a malformed profile must not degrade into
    "run everything" (a surprise red wall) or into "run nothing" (a silent
    ungating). It stops the gate and names the line to fix.
    """


@dataclasses.dataclass(frozen=True)
class SurfaceProfile:
    surface: str
    mode: str = MODE_STAPEL
    #: shell command that IS the gate when ``mode == "native"``
    command: str = ""
    #: written justification, mandatory when ``mode == "off"``
    reason: str = ""

    def describe(self) -> str:
        if self.mode == MODE_NATIVE:
            return f"native gate: {self.command}"
        if self.mode == MODE_OFF:
            return f"off — {self.reason}"
        return "stapel arsenal"


@dataclasses.dataclass(frozen=True)
class LintProfile:
    surfaces: dict[str, SurfaceProfile]
    #: rule id -> written reason. Findings carrying the id are dropped from
    #: every stapel linter's report, and the waiver is echoed as a note.
    waivers: dict[str, str] = dataclasses.field(default_factory=dict)
    #: where it was read from ("" when no file exists — the default profile)
    path: str = ""

    @property
    def present(self) -> bool:
        return bool(self.path)

    def for_surface(self, surface: str) -> SurfaceProfile:
        return self.surfaces.get(surface) or SurfaceProfile(surface)

    def for_linter(self, linter_name: str) -> SurfaceProfile:
        return self.for_surface(LINTER_SURFACES.get(linter_name, "python"))

    def native_surfaces(self) -> list[SurfaceProfile]:
        return [
            self.surfaces[s] for s in SURFACES
            if s in self.surfaces and self.surfaces[s].mode == MODE_NATIVE
        ]

    def summary(self) -> list[str]:
        """One line per surface that is NOT on the stapel arsenal, plus one
        per waiver — what a reader must see to know what was not checked."""
        lines = [
            f"surface {p.surface}: {p.describe()}"
            for p in (self.for_surface(s) for s in SURFACES)
            if p.mode != MODE_STAPEL
        ]
        lines += [f"waiver {rule}: {reason}" for rule, reason in sorted(self.waivers.items())]
        return lines


def default_profile() -> LintProfile:
    """No file ⇒ the full arsenal on every surface. The generated-project
    case, and the safe answer for anything unrecognised."""
    return LintProfile(surfaces={s: SurfaceProfile(s) for s in SURFACES})


def profile_path(project: Path) -> Path:
    return Path(project) / PROFILE_FILENAME


def load_profile(project: Path) -> LintProfile:
    """Read ``<project>/stapel-lint.toml``; return the default profile when
    absent. Raises :class:`LintProfileError` on anything malformed."""
    path = profile_path(project)
    if not path.is_file():
        return default_profile()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise LintProfileError(f"{path}: not readable as TOML — {exc}") from exc
    return parse_profile(raw, path=str(path))


def parse_profile(raw: dict, *, path: str = "") -> LintProfile:
    """Validate a decoded profile mapping. Kept separate from file IO so a
    caller holding the declaration in memory (Studio's importer seeding one
    into a repo) validates through the same code the gate reads it with."""
    where = path or f"<{PROFILE_FILENAME}>"
    surface_section = raw.get("surface", {})
    if not isinstance(surface_section, dict):
        raise LintProfileError(f"{where}: [surface] must be a table of surfaces")

    unknown = sorted(set(surface_section) - set(SURFACES))
    if unknown:
        raise LintProfileError(
            f"{where}: unknown surface(s) {', '.join(unknown)} — "
            f"known surfaces are {', '.join(SURFACES)}"
        )

    surfaces: dict[str, SurfaceProfile] = {s: SurfaceProfile(s) for s in SURFACES}
    for name, body in surface_section.items():
        if not isinstance(body, dict):
            raise LintProfileError(f"{where}: [surface.{name}] must be a table")
        mode = str(body.get("mode", MODE_STAPEL)).strip()
        if mode not in MODES:
            raise LintProfileError(
                f"{where}: [surface.{name}].mode = {mode!r} — expected one of "
                f"{', '.join(MODES)}"
            )
        command = str(body.get("command", "") or "").strip()
        reason = str(body.get("reason", "") or "").strip()
        if mode == MODE_NATIVE and not command:
            raise LintProfileError(
                f"{where}: [surface.{name}].mode = \"native\" needs a "
                f"`command` — the project's own linter has to be named, or "
                f"nothing runs and nothing says so"
            )
        if mode == MODE_OFF and not reason:
            raise LintProfileError(
                f"{where}: [surface.{name}].mode = \"off\" needs a `reason` — "
                f"an ungated surface is allowed, an unexplained one is not"
            )
        surfaces[name] = SurfaceProfile(name, mode, command, reason)

    waiver_section = raw.get("waivers", {})
    if not isinstance(waiver_section, dict):
        raise LintProfileError(f"{where}: [waivers] must be a table of rule = \"reason\"")
    waivers: dict[str, str] = {}
    for rule, reason in waiver_section.items():
        text = str(reason or "").strip()
        if not text:
            raise LintProfileError(
                f"{where}: [waivers].{rule} has no reason — same rule as an "
                f"off surface: state the exception or drop it"
            )
        waivers[str(rule)] = text

    return LintProfile(surfaces=surfaces, waivers=waivers, path=path)


# ---------------------------------------------------------------------------
# rendering — Studio seeds a profile into an imported repo through this
# ---------------------------------------------------------------------------

_HEADER = f"""\
# {PROFILE_FILENAME} — per-project lint profile (stapel-tools).
#
# mode per surface:
#   stapel  run the stapel lint arsenal (the default; what a generated
#           project gets with no file at all)
#   native  the project's OWN linter is the gate — `command` is mandatory
#   off     the surface is not gated — `reason` is mandatory
#
# Every non-stapel surface is REPORTED by stapel-verify, with its reason.
# Turning a gate off is allowed; turning it off quietly is not.
"""


def render_toml(profile: LintProfile) -> str:
    """Render a profile back to the file format, comments included.

    Round-trips through :func:`parse_profile` — the writer cannot emit a file
    the reader would reject (``test_lint_profile.py`` proves it).
    """
    out = [_HEADER]
    for surface in SURFACES:
        p = profile.for_surface(surface)
        if p.mode == MODE_STAPEL:
            continue
        out.append(f"# {SURFACES[surface]}")
        out.append(f"[surface.{surface}]")
        out.append(f'mode = "{p.mode}"')
        if p.command:
            out.append(f'command = {_toml_str(p.command)}')
        if p.reason:
            out.append(f'reason = {_toml_str(p.reason)}')
        out.append("")
    if profile.waivers:
        out.append("# single rule ids, each with the reason it is waived")
        out.append("[waivers]")
        for rule, reason in sorted(profile.waivers.items()):
            out.append(f"{rule} = {_toml_str(reason)}")
        out.append("")
    return "\n".join(out)


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
