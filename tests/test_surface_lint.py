"""stapel-surface-lint tests — the pre-merge gate against reinvention.

Every rule is exercised in the shape of the incident that motivated it, and —
more important — in the shapes that must stay quiet. The rules in this family
have exactly one way to fail in practice: reporting something legitimate often
enough that somebody switches them off. So each ``_quiet`` test below is a
measured false positive from the fleet run, pinned so it cannot come back.
"""
import json

import pytest

from stapel_tools import surface_lint
from stapel_tools.surface_lint import lint_project, main

CORE_SURFACE = [
    {
        "name": "IsNotAnonymousUser",
        "kind": "permission_class",
        "path": "stapel_core.django.api.permissions.IsNotAnonymousUser",
        "intent": "The write-gate for any endpoint that needs a REAL account.",
        "instead_of": ["rest_framework.permissions.IsAuthenticated"],
    },
    {
        "name": "IsStaffUser",
        "kind": "permission_class",
        "path": "stapel_core.django.api.permissions.IsStaffUser",
        "intent": "Staff-or-superuser gate.",
    },
]

AGENT_SURFACE = [
    {
        "name": "redaction_gate",
        "kind": "gate_function",
        "path": "stapel_agent.safety.redaction.redaction_gate",
        "intent": "Call before writing ANY model-produced text to a durable artifact.",
    },
]

AUTH_SURFACE = [
    {
        "name": "RegistrationCapabilities.email_mock",
        "kind": "capability_field",
        "path": "stapel_auth.oauth.dto.RegistrationCapabilities.email_mock",
        "intent": "Published so a host frontend renders a dev-mode badge.",
        "consumer": "frontend",
    },
]


def make_module(workspace, name, surface, *, version="1.0.0"):
    repo = workspace / name
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "capabilities.json").write_text(json.dumps({
        "module": name,
        "version": version,
        "provides": "fixture",
        "axes": [],
        "extension_points": [],
        "surface": surface,
        "requires": [],
    }))
    return repo


def make_react_package(workspace, short, *, sources: dict):
    pkg = workspace / "stapel-react" / "packages" / f"{short}-react"
    (pkg / "src" / "api" / "generated").mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": f"@stapel/{short}-react"}))
    (pkg / "src" / "api" / "generated" / "schema.ts").write_text(
        "export interface Caps { email_mock?: boolean; phone_mock?: boolean }\n"
    )
    for rel, text in sources.items():
        target = pkg / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return pkg


@pytest.fixture
def workspace(tmp_path):
    make_module(tmp_path, "stapel-core", CORE_SURFACE)
    make_module(tmp_path, "stapel-agent", AGENT_SURFACE)
    return tmp_path


def run(project, workspace, **kwargs):
    notes = kwargs.pop("notes", [])
    return lint_project(
        project, search_roots=[workspace], use_installed=False, notes=notes, **kwargs
    )


def codes(findings):
    return sorted(f.rule for f in findings)


# ---------------------------------------------------------------------------
# SUR001 — duplicate-of-surface
# ---------------------------------------------------------------------------


def test_sur001_fires_on_the_incident(workspace):
    """The prototype: ``marketplace-common-python`` kept its own copy of the six
    permission classes stapel-core publishes, name for name."""
    proj = workspace / "proj"
    proj.mkdir()
    (proj / "permissions.py").write_text(
        "from rest_framework import permissions\n\n\n"
        "class IsNotAnonymousUser(permissions.BasePermission):\n"
        "    def has_permission(self, request, view):\n"
        "        return not getattr(request.user, 'is_anonymous', False)\n"
    )
    findings = run(proj, workspace)
    assert codes(findings) == ["SUR001"]
    message = findings[0].message
    assert "IsNotAnonymousUser" in message
    assert "stapel_core.django.api.permissions.IsNotAnonymousUser" in message
    assert findings[0].line == 4


def test_sur001_quiet_for_a_products_own_domain_permission(workspace):
    """Measured false positive of the design's broader form: every product owns
    domain permissions (``IsWorkspaceAdmin``, ``IsAdOwner``,
    ``IsReportModerator``) that duplicate nothing."""
    proj = workspace / "proj"
    proj.mkdir()
    (proj / "permissions.py").write_text(
        "from rest_framework.permissions import BasePermission\n\n\n"
        "class IsWorkspaceAdmin(BasePermission):\n"
        "    def has_permission(self, request, view):\n"
        "        return request.user.is_workspace_admin\n"
    )
    assert run(proj, workspace) == []


def test_sur001_quiet_inside_a_vendored_checkout(workspace):
    """``stapel-studio`` keeps ``.vendor/stapel-core/`` — the library's own
    definition is the publication, not a copy of it (6 findings before the
    filter existed)."""
    proj = workspace / "proj"
    vendored = proj / ".vendor" / "stapel-core" / "django" / "api"
    vendored.mkdir(parents=True)
    (vendored / "permissions.py").write_text(
        "from rest_framework import permissions\n\n\n"
        "class IsNotAnonymousUser(permissions.BasePermission):\n"
        "    pass\n"
    )
    assert run(proj, workspace) == []


def test_sur001_quiet_for_the_publishing_module_itself(tmp_path):
    """Linting stapel-core must not report stapel-core's own surface."""
    core = make_module(tmp_path, "stapel-core", CORE_SURFACE)
    (core / "pyproject.toml").write_text(
        '[project]\nname = "stapel-core"\nversion = "1.0.0"\n'
    )
    perms = core / "django" / "api"
    perms.mkdir(parents=True)
    (perms / "permissions.py").write_text(
        "from rest_framework import permissions\n\n\n"
        "class IsNotAnonymousUser(permissions.BasePermission):\n"
        "    pass\n"
    )
    assert run(core, tmp_path) == []


# ---------------------------------------------------------------------------
# SUR002 — instead_of
# ---------------------------------------------------------------------------


VIEW_FROM_IMPORT = (
    "from rest_framework.permissions import IsAuthenticated\n"
    "from rest_framework.views import APIView\n\n\n"
    "class MyView(APIView):\n"
    "    permission_classes = [IsAuthenticated]\n"
)

VIEW_ATTRIBUTE_IMPORT = (
    "from rest_framework import permissions\n"
    "from rest_framework.views import APIView\n\n\n"
    "class MyView(APIView):\n"
    "    permission_classes = [permissions.IsAuthenticated]\n"
)


@pytest.mark.parametrize("source", [VIEW_FROM_IMPORT, VIEW_ATTRIBUTE_IMPORT])
def test_sur002_fires_on_both_import_idioms(workspace, source):
    """``permissions.IsAuthenticated`` is the majority spelling in the fleet
    (35 view classes in one product) — resolving only the ``from ... import``
    form would have been an accidental narrowing, not a decided one."""
    proj = workspace / "proj"
    proj.mkdir()
    (proj / "views.py").write_text(source)
    findings = run(proj, workspace)
    assert codes(findings) == ["SUR002"]
    assert "IsNotAnonymousUser" in findings[0].message


def test_sur002_quiet_when_the_project_uses_the_replacement(workspace):
    """The per-call-site form of this rule produced 13 findings in one product,
    almost all of them a deliberate 'this endpoint is open to guests'. A project
    that reaches for the replacement anywhere is making that call itself."""
    proj = workspace / "proj"
    proj.mkdir()
    (proj / "views.py").write_text(VIEW_ATTRIBUTE_IMPORT)
    (proj / "other.py").write_text(
        "from stapel_core.django.api.permissions import IsNotAnonymousUser\n"
        "from rest_framework.views import APIView\n\n\n"
        "class Other(APIView):\n"
        "    permission_classes = [IsNotAnonymousUser]\n"
    )
    assert run(proj, workspace) == []


def test_sur002_quiet_for_a_same_named_local_class(workspace):
    """A local ``IsAuthenticated`` is not DRF's; the file's own imports decide."""
    proj = workspace / "proj"
    proj.mkdir()
    (proj / "views.py").write_text(
        "from .mine import IsAuthenticated\n"
        "from rest_framework.views import APIView\n\n\n"
        "class MyView(APIView):\n"
        "    permission_classes = [IsAuthenticated]\n"
    )
    assert run(proj, workspace) == []


def test_sur002_reports_once_per_displaced_symbol(workspace):
    """One finding per displaced symbol, not per call site."""
    proj = workspace / "proj"
    proj.mkdir()
    for name in ("a", "b", "c"):
        (proj / f"views_{name}.py").write_text(VIEW_ATTRIBUTE_IMPORT)
    assert codes(run(proj, workspace)) == ["SUR002"]


# ---------------------------------------------------------------------------
# SUR003 — imported-but-never-called
# ---------------------------------------------------------------------------


GATE_IMPORT = "from stapel_agent.safety.redaction import RedactionError, redaction_gate\n"


def test_sur003_fires_on_the_incident(workspace):
    """The prototype, verbatim in shape: the port kept the import and lost the
    call, so the protection became its own appearance."""
    proj = workspace / "proj"
    proj.mkdir()
    (proj / "mic_stage.py").write_text(
        GATE_IMPORT + "\n\ndef write(text):\n    return open('a', 'w').write(text)\n"
    )
    findings = run(proj, workspace)
    assert codes(findings) == ["SUR003"]
    assert "redaction_gate" in findings[0].message
    assert findings[0].line == 1


def test_sur003_quiet_when_called(workspace):
    proj = workspace / "proj"
    proj.mkdir()
    (proj / "mic_stage.py").write_text(
        GATE_IMPORT + "\n\ndef write(text):\n    redaction_gate(text)\n"
    )
    assert run(proj, workspace) == []


@pytest.mark.parametrize(
    "filename,body",
    [
        # a re-export hub binds names it never calls, by design
        ("__init__.py", ""),
        # the same intent, stated explicitly
        ("api.py", '\n__all__ = ["redaction_gate"]\n'),
        # binds nothing at runtime
        ("typed.py", None),
        # a deferred call is a call
        ("registry.py", "\nGATES = [redaction_gate]\n"),
        ("partial.py", "\nimport functools\nrun = functools.partial(redaction_gate)\n"),
        ("decorated.py", "\nHOOKS = {'write': redaction_gate}\n"),
    ],
)
def test_sur003_quiet_for_legitimate_no_call_shapes(workspace, filename, body):
    proj = workspace / "proj"
    proj.mkdir()
    if body is None:
        source = (
            "from typing import TYPE_CHECKING\n\n"
            "if TYPE_CHECKING:\n"
            "    from stapel_agent.safety.redaction import redaction_gate\n"
        )
    else:
        source = GATE_IMPORT + body
    (proj / filename).write_text(source)
    assert run(proj, workspace) == []


def test_sur003_quiet_for_a_same_named_local_gate(workspace):
    """Only an import from the OWNING package counts — a project's own
    ``redaction_gate`` is its own business."""
    proj = workspace / "proj"
    proj.mkdir()
    (proj / "mic_stage.py").write_text(
        "from .local import redaction_gate\n\n\ndef write(text):\n    pass\n"
    )
    assert run(proj, workspace) == []


def test_sur003_quiet_for_a_side_effect_import(workspace):
    """``import stapel_agent.safety.redaction`` binds no gate name — the
    registration idiom is out of scope by construction."""
    proj = workspace / "proj"
    proj.mkdir()
    (proj / "apps.py").write_text("import stapel_agent.safety.redaction  # noqa: F401\n")
    assert run(proj, workspace) == []


# ---------------------------------------------------------------------------
# SUR004 — publisher-without-consumer
# ---------------------------------------------------------------------------


def test_sur004_fires_on_the_incident(tmp_path):
    """The prototype: ``email_mock`` exists in the generated OpenAPI types of
    ``auth-react`` and nowhere else, so the screen says 'code sent' when nothing
    was sent."""
    auth = make_module(tmp_path, "stapel-auth", AUTH_SURFACE)
    (auth / "pyproject.toml").write_text(
        '[project]\nname = "stapel-auth"\nversion = "1.0.0"\n'
    )
    make_react_package(tmp_path, "auth", sources={
        "src/LoginPanel.tsx": "export const Panel = () => <div>login</div>;\n",
    })
    findings = run(auth, tmp_path)
    assert codes(findings) == ["SUR004"]
    assert "email_mock" in findings[0].message
    assert findings[0].path.endswith("auth-react/package.json")


def test_sur004_quiet_when_hand_written_code_reads_the_field(tmp_path):
    auth = make_module(tmp_path, "stapel-auth", AUTH_SURFACE)
    (auth / "pyproject.toml").write_text(
        '[project]\nname = "stapel-auth"\nversion = "1.0.0"\n'
    )
    make_react_package(tmp_path, "auth", sources={
        "src/LoginPanel.tsx": "export const Panel = ({caps}) => caps.email_mock ? 'dev' : '';\n",
    })
    assert run(auth, tmp_path) == []


def test_sur004_not_reported_to_an_unrelated_repository(tmp_path):
    """Before this narrowing the rule cost 78 findings across the workspace to
    say the same 2 things — every repo re-reporting auth-react's gap."""
    make_module(tmp_path, "stapel-auth", AUTH_SURFACE)
    make_react_package(tmp_path, "auth", sources={})
    other = tmp_path / "stapel-video"
    other.mkdir()
    (other / "pyproject.toml").write_text(
        '[project]\nname = "stapel-video"\nversion = "1.0.0"\n'
    )
    assert run(other, tmp_path) == []


def test_sur004_reported_to_the_consumer_repository(tmp_path):
    """The ``-react`` monorepo owes the reading, so it is told too."""
    make_module(tmp_path, "stapel-auth", AUTH_SURFACE)
    make_react_package(tmp_path, "auth", sources={})
    react = tmp_path / "stapel-react"
    findings = run(react, tmp_path)
    assert codes(findings) == ["SUR004"]


def test_sur004_notes_a_missing_consumer_checkout(tmp_path):
    auth = make_module(tmp_path, "stapel-auth", AUTH_SURFACE)
    (auth / "pyproject.toml").write_text(
        '[project]\nname = "stapel-auth"\nversion = "1.0.0"\n'
    )
    notes = []
    findings = lint_project(auth, search_roots=[tmp_path], use_installed=False, notes=notes)
    assert findings == []
    assert any("auth-react" in note for note in notes)


# ---------------------------------------------------------------------------
# index + CLI
# ---------------------------------------------------------------------------


def test_no_surface_visible_skips_every_rule(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "permissions.py").write_text(
        "from rest_framework import permissions\n\n\n"
        "class IsNotAnonymousUser(permissions.BasePermission):\n"
        "    pass\n"
    )
    notes = []
    assert lint_project(proj, search_roots=[tmp_path], use_installed=False, notes=notes) == []
    assert any("all SUR rules skipped" in note for note in notes)


def test_index_prefers_one_document_per_module(tmp_path):
    """A module checked out twice (workspace + a nested copy) must not double
    its entries."""
    make_module(tmp_path, "stapel-core", CORE_SURFACE)
    nested = tmp_path / "nested"
    nested.mkdir()
    make_module(nested, "stapel-core", CORE_SURFACE, version="9.9.9")
    proj = tmp_path / "proj"
    proj.mkdir()
    index = surface_lint.load_surface_index(
        proj, search_roots=[tmp_path, nested], use_installed=False
    )
    assert [entry["name"] for entry in index] == ["IsNotAnonymousUser", "IsStaffUser"]


def test_cli_exit_codes_and_json(workspace, capsys):
    proj = workspace / "proj"
    proj.mkdir()
    (proj / "permissions.py").write_text(
        "from rest_framework import permissions\n\n\n"
        "class IsStaffUser(permissions.BasePermission):\n"
        "    pass\n"
    )
    rc = main([str(proj), "--workspace", str(workspace), "--no-installed", "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["errors"] == 1
    assert payload["findings"][0]["rule"] == "SUR001"

    rc = main([str(workspace / "stapel-core"), "--workspace", str(workspace), "--no-installed"])
    assert rc == 0


def test_cli_rejects_a_non_directory(tmp_path, capsys):
    assert main([str(tmp_path / "nope")]) == 2
