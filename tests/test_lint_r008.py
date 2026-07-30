"""R008 — a lifecycle/security flag in ``get_or_create(defaults=…)``.

``defaults`` seeds a row that is being CREATED. Written with ``is_active`` /
``is_verified`` / ``is_staff`` / ``*_required`` in it, the call reads as an
assertion about the object and is not one: on the get branch the dict is never
touched, so the caller silently accepts whatever the stored row says. The
canon is the opposite shape — put the invariant on the point of use once
(stapel-auth gates session issuance on the live account state), not on every
creation site.

Warning-level on purpose: seeding a flag only at creation is often exactly
right, and an error-level rule on a legitimate idiom gets silenced wholesale.
"""
from stapel_tools.lint import scan_file


def _scan(tmp_path, source, name="services.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return [v for v in scan_file(str(path)) if v.rule == "R008"]


def test_get_or_create_with_is_active_in_defaults(tmp_path):
    v = _scan(tmp_path, (
        "def login(email):\n"
        "    user, created = User.objects.get_or_create(\n"
        "        email=email, defaults={'is_active': True, 'first_name': ''},\n"
        "    )\n"
        "    return user\n"
    ))
    assert len(v) == 1
    assert v[0].level == "warning"
    assert "'is_active'" in v[0].message
    assert "ONLY when the object is created" in v[0].message


def test_update_or_create_gets_the_mirror_message(tmp_path):
    v = _scan(tmp_path, (
        "def enroll(user, secret):\n"
        "    Device.objects.update_or_create(\n"
        "        user=user, defaults={'secret': secret, 'is_active': False},\n"
        "    )\n"
    ))
    assert len(v) == 1
    assert "the row it FOUND too" in v[0].message


def test_all_named_flag_families(tmp_path):
    for flag in ("is_active", "is_verified", "is_staff", "admit_required", "mfa_required"):
        v = _scan(
            tmp_path,
            f"M.objects.get_or_create(k=1, defaults={{{flag!r}: True}})\n",
            name=f"svc_{flag}.py",
        )
        assert len(v) == 1, flag
        assert repr(flag) in v[0].message


def test_several_flags_are_one_finding_naming_all(tmp_path):
    v = _scan(tmp_path, (
        "M.objects.get_or_create(k=1, defaults={'is_active': True, 'is_staff': True})\n"
    ))
    assert len(v) == 1
    assert "'is_active'" in v[0].message and "'is_staff'" in v[0].message


def test_ordinary_defaults_are_clean(tmp_path):
    assert _scan(tmp_path, (
        "M.objects.get_or_create(\n"
        "    k=1, defaults={'title': 'x', 'created_by': u, 'workspace_id': w},\n"
        ")\n"
    )) == []


def test_flag_outside_defaults_is_not_the_pattern(tmp_path):
    """Filtering ON the flag is a lookup, not a seed — it is fine."""
    assert _scan(tmp_path, (
        "M.objects.get_or_create(k=1, is_active=True, defaults={'title': 'x'})\n"
    )) == []


def test_noqa_suppresses(tmp_path):
    assert _scan(tmp_path, (
        "M.objects.get_or_create(k=1, defaults={'is_active': True})  # noqa: R008\n"
    )) == []


def test_rule_runs_outside_views_too(tmp_path):
    """get_or_create lives in services, consumers, actions, commands — the
    rule must not be routed to a single layer."""
    for name in ("views.py", "services.py", "actions.py", "consumers.py"):
        v = _scan(
            tmp_path,
            "M.objects.get_or_create(k=1, defaults={'is_active': True})\n",
            name=name,
        )
        assert len(v) == 1, name


def test_r008_never_fails_the_build(tmp_path):
    """The whole point of warning-level: nothing here is an error, so the
    CLI's ``sys.exit(1 if errors else 0)`` stays 0."""
    from stapel_tools import lint

    src = tmp_path / "svc.py"
    src.write_text(
        "M.objects.get_or_create(k=1, defaults={'is_active': True})\n", encoding="utf-8"
    )
    violations = lint.scan_paths([str(tmp_path)])
    assert [v.rule for v in violations] == ["R008"]
    assert [v for v in violations if v.level == "error"] == []
