"""The local bootstrap admin can reach the moderation queue after first login.

``createsuperuser --noinput`` set no email on the stapel user model and the
SPA signs in by email/phone code only; no moderation role existed, and an
OTP account made staff got 403 "no moderation clearance". A selection with
moderation now defines the module's documented roles and its bootstrap.sh
runs ``ensure_moderation_lead`` (email + role, prototype stage only).
"""
import ast

from stapel_tools import _wiring
from stapel_tools.create_project import create_project


def _svc(tmp_path, modules):
    create_project(
        name="app", project_type="monolith", title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules,
        output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    return tmp_path / "app" / "svc-app"


def test_classified_seeds_the_moderation_lead(tmp_path):
    svc = _svc(tmp_path, ["classified"])
    cmd = svc / "apps" / "app" / "management" / "commands" / "ensure_moderation_lead.py"
    source = cmd.read_text()
    ast.parse(source)
    assert 'ROLE = "moderation_lead"' in source
    assert "stage != \"prototype\"" in source and "assign_staff_role" in source
    assert (svc / "apps" / "app" / "management" / "__init__.py").is_file()
    boot = (svc / "bootstrap.sh").read_text()
    superuser_at = boot.index('optional "superuser" ensure_superuser')
    lead_at = boot.index('optional "moderation-lead" python manage.py ensure_moderation_lead')
    assert superuser_at < lead_at
    assert '--email "${DJANGO_SUPERUSER_EMAIL:-}"' in boot
    base = (svc / "config" / "settings" / "base.py").read_text()
    assert '"moderation_lead": {"clearance": "low", "apps": {"moderation": "high"}}' in base


def test_the_demo_credentials_live_in_the_local_env_only(tmp_path):
    proj = _svc(tmp_path, ["classified"]).parent
    assert "DJANGO_SUPERUSER_EMAIL=" in (proj / ".env.local").read_text()
    assert "DJANGO_SUPERUSER" not in (proj / ".env.example").read_text()


def test_without_moderation_nothing_is_seeded(tmp_path):
    svc = _svc(tmp_path, ["shop"])
    assert not (svc / "apps" / "app" / "management").exists()
    assert "moderation-lead" not in (svc / "bootstrap.sh").read_text()


def test_roles_merge_under_explicit_entries():
    ns = {"STAPEL_ACCESS": {"STRICT": True, "ROLES": {"moderator": {"clearance": "mid"}}}}
    exec(_wiring.MODERATION_ROLES, ns)  # noqa: S102
    assert ns["STAPEL_ACCESS"]["STRICT"] is True
    assert ns["STAPEL_ACCESS"]["ROLES"]["moderator"] == {"clearance": "mid"}
    assert ns["STAPEL_ACCESS"]["ROLES"]["moderation_lead"]["apps"] == {"moderation": "high"}


def test_roles_come_with_the_mandate_backend():
    ns = {"AUTHENTICATION_BACKENDS": ["django.contrib.auth.backends.ModelBackend"]}
    exec(_wiring.MODERATION_ROLES, ns)  # noqa: S102
    assert ns["AUTHENTICATION_BACKENDS"] == [
        "stapel_core.access.backend.MandateBackend",
        "stapel_core.access.backend.AuditedModelBackend",
    ]
    exec(_wiring.MODERATION_ROLES, ns)  # noqa: S102  (idempotent)
    assert ns["AUTHENTICATION_BACKENDS"].count("stapel_core.access.backend.MandateBackend") == 1
