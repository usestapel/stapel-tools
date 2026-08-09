"""SWAP003 — a hardcoded dotted path into somebody else's package.

Every test asserts BOTH directions: the rule fires on the real shape AND
stays silent on each look-alike. That is not symmetry for its own sake. An
earlier draft of this rule read only the *arguments* of ``import_string`` and
``apps.is_installed``; the incident it was written for wraps both behind one
helper and passes the literal to the helper, so the draft found a variable,
cleared the file that contained the defect, and reported zero across the
fleet — a dead rule that ships looking healthy.

The negative half is also the design: a dotted path IS the fleet's extension
mechanism (``STAPEL_RECORDINGS["STORAGE"]``, ``NORMALIZER``,
``PIPELINE_RESOLVER``, the GDPR provider registry, merge-registries keyed by
kind), and a rule that cannot tell a settings-sourced path from a hardcoded
one is a rule that gets silenced wholesale.
"""
from pathlib import Path

from stapel_tools.swap_lint import find_swap003, lint_project

PYPROJECT = """\
[project]
name = "stapel-thing"
version = "0.1.0"
dependencies = [{deps}]
"""


def build(tmp_path, files, deps=(), extras=None):
    """A one-package distribution named ``stapel-thing`` (package
    ``stapel_thing``), plus whatever files the test needs."""
    root = tmp_path / f"proj{len(list(tmp_path.iterdir()))}"
    root.mkdir()
    text = PYPROJECT.format(deps=", ".join(f'"{d}"' for d in deps))
    if extras:
        text += "\n[project.optional-dependencies]\n"
        for name, specs in extras.items():
            text += f"{name} = [" + ", ".join(f'"{s}"' for s in specs) + "]\n"
    (root / "pyproject.toml").write_text(text, encoding="utf-8")
    pkg = root / "stapel_thing"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for rel, src in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src, encoding="utf-8")
    return root


def hits(tmp_path, source, rel="stapel_thing/services.py", **kwargs):
    root = build(tmp_path, {rel: source}, **kwargs)
    return [(Path(v.path).name, v.line) for v in find_swap003(root)]


def paths(tmp_path, source, **kwargs):
    root = build(tmp_path, {"stapel_thing/services.py": source}, **kwargs)
    return sorted(
        v.message.split("'")[1] for v in find_swap003(root)
    )


# --- fires: the resolution sinks ----------------------------------------


def test_import_string_of_a_foreign_symbol_is_flagged(tmp_path):
    source = (
        "from django.utils.module_loading import import_string\n"
        "\n"
        "def canon():\n"
        "    return import_string('other_pkg.validators.validate_display_name')\n"
    )
    assert ("services.py", 4) in hits(tmp_path, source)


def test_import_module_of_a_foreign_package_is_flagged(tmp_path):
    source = (
        "import importlib\n"
        "\n"
        "def load():\n"
        "    return importlib.import_module('other_pkg.thing')\n"
    )
    assert ("services.py", 4) in hits(tmp_path, source)


def test_app_registry_lookup_of_a_foreign_label_is_flagged(tmp_path):
    source = (
        "from django.apps import apps\n"
        "\n"
        "def model():\n"
        "    return apps.get_model('other_pkg', 'Profile')\n"
    )
    assert ("services.py", 4) in hits(tmp_path, source)


def test_presence_probe_for_a_foreign_package_is_flagged(tmp_path):
    """The first half of the incident: 'does that module run in MY process'.

    Not a symbol resolution, but the same defect — the answer is a property
    of the topology, and there is no remote form of the question either.
    """
    source = (
        "from django.apps import apps\n"
        "from importlib.util import find_spec\n"
        "\n"
        "def here():\n"
        "    return apps.is_installed('other_pkg') and find_spec('other_pkg')\n"
    )
    assert [("services.py", 5), ("services.py", 5)] == hits(tmp_path, source)


def test_getattr_on_a_foreign_module_object_is_flagged(tmp_path):
    source = (
        "import other_pkg.models\n"
        "\n"
        "def accessor():\n"
        "    return getattr(other_pkg.models, 'get_profile_model')\n"
    )
    assert ("services.py", 4) in hits(tmp_path, source)


def test_a_module_level_constant_is_still_a_literal(tmp_path):
    """``_PROFILES_APP_LABEL = "stapel_profiles"`` then
    ``apps.is_installed(_PROFILES_APP_LABEL)`` — the incident wrote it exactly
    this way, with a comment explaining that a label is not an import."""
    source = (
        "from django.apps import apps\n"
        "\n"
        "_FOREIGN_APP_LABEL = 'other_pkg'\n"
        "\n"
        "def here():\n"
        "    return apps.is_installed(_FOREIGN_APP_LABEL)\n"
    )
    assert ("services.py", 6) in hits(tmp_path, source)


def test_a_one_level_helper_does_not_hide_the_literal(tmp_path):
    """The shape that killed the first draft.

    ``profiles_in_process(dotted_path)`` wraps the registry probe and the
    ``import_string`` behind one honest name; every hardcoded path then sits
    at a *call* to that helper, and the resolver itself only ever sees a
    parameter.
    """
    source = (
        "from django.apps import apps\n"
        "from django.utils.module_loading import import_string\n"
        "\n"
        "def in_process(dotted_path):\n"
        "    if not apps.is_installed('stapel_thing'):\n"
        "        return None\n"
        "    return import_string(dotted_path)\n"
        "\n"
        "def canon():\n"
        "    return in_process('other_pkg.validators.validate_display_name')\n"
        "\n"
        "def model():\n"
        "    return in_process('other_pkg.models.get_profile_model')\n"
    )
    assert hits(tmp_path, source) == [("services.py", 10), ("services.py", 13)]


def test_the_message_names_the_foreign_path(tmp_path):
    source = (
        "from django.utils.module_loading import import_string\n"
        "x = import_string('other_pkg.a.B')\n"
    )
    assert paths(tmp_path, source) == ["other_pkg.a.B"]


# --- silent: the value did not come from a literal here -------------------


def test_a_settings_sourced_path_is_never_flagged(tmp_path):
    """The whole design rests on this exemption.

    A dotted path that arrives from CONFIGURATION points at an extension
    point the module declares and a target the HOST chose — the dotted path
    IS the mechanism. None of these hand a string constant to the resolver,
    so the rule never sees them.
    """
    source = (
        "from django.conf import settings\n"
        "from django.utils.module_loading import import_string\n"
        "\n"
        "def storage():\n"
        "    return import_string(settings.STAPEL_RECORDINGS['STORAGE'])\n"
        "\n"
        "def normalizer():\n"
        "    return import_string(getattr(settings, 'NORMALIZER', 'other_pkg.norm'))\n"
        "\n"
        "def resolver():\n"
        "    path = getattr(settings, 'PIPELINE_RESOLVER', None)\n"
        "    return import_string(path) if path else None\n"
        "\n"
        "def provider(kind):\n"
        "    return import_string(settings.GDPR_PROVIDERS[kind])\n"
    )
    assert hits(tmp_path, source) == []


def test_a_swap_seam_default_is_not_a_resolution(tmp_path):
    """``get_model(KEY, default="...")`` is the swap registry SWAP001 guards:
    a literal waiting for a host to override it, not a path being resolved."""
    source = (
        "from stapel_core.django.swappable import get_model\n"
        "\n"
        "PROFILE_KEY = 'THING_PROFILE_MODEL'\n"
        "DEFAULTS = {'STORAGE': 'other_pkg.storage.Backend'}\n"
        "\n"
        "def profile():\n"
        "    return get_model(PROFILE_KEY, default='stapel_thing.models.Profile')\n"
    )
    assert hits(tmp_path, source) == []


def test_a_same_package_literal_is_not_flagged(tmp_path):
    """'Only to your own overridable entities' — resolving your own symbol by
    string is how a module declares its own extension point."""
    source = (
        "from django.apps import apps\n"
        "from django.utils.module_loading import import_string\n"
        "\n"
        "def own():\n"
        "    apps.is_installed('stapel_thing')\n"
        "    return import_string('stapel_thing.validators.validate')\n"
    )
    assert hits(tmp_path, source) == []


def test_the_framework_is_not_a_foreign_module(tmp_path):
    """``apps.is_installed("django.contrib.admin")`` asks whether the HOST
    turned admin on. That is a question about configuration, not topology:
    django.contrib ships inside the Django that is executing the line."""
    source = (
        "from django.apps import apps\n"
        "\n"
        "def visible():\n"
        "    return (apps.is_installed('django.contrib.admin')\n"
        "            and apps.is_installed('django.contrib.auth'))\n"
    )
    assert hits(tmp_path, source) == []


def test_the_standard_library_is_not_a_foreign_module(tmp_path):
    source = (
        "import importlib\n"
        "\n"
        "def load():\n"
        "    return importlib.import_module('json')\n"
    )
    assert hits(tmp_path, source) == []


def test_a_declared_dependency_is_not_a_hidden_import(tmp_path):
    """The charge is *hidden* import: undeclared, unconstrained, no failure
    until runtime. A package pinned in the manifest — including in an extra,
    the shape of ``stapel-cdn``'s optional ``pyvips`` probe — is none of
    those, and the same probe stays flagged when nothing declares it."""
    source = (
        "from importlib.util import find_spec\n"
        "\n"
        "def images():\n"
        "    return find_spec('pyvips') is not None\n"
    )
    assert hits(tmp_path, source, extras={"images": ["pyvips>=2.2"]}) == []
    assert hits(tmp_path, source) == [("services.py", 4)]


def test_a_requirements_file_below_the_root_also_declares(tmp_path):
    """``ironmemo-backend`` pins ``meeteval==0.4.3`` one directory down; a
    root-only glob reads the repo as declaring nothing and flags a lazy
    import of its own pinned dependency."""
    source = (
        "import importlib\n"
        "\n"
        "def evaluator():\n"
        "    return importlib.import_module('meeteval.io')\n"
    )
    root = build(tmp_path, {"stapel_thing/services.py": source})
    (root / "bench").mkdir()
    (root / "bench" / "requirements.txt").write_text(
        "# pinned\nmeeteval==0.4.3\n", encoding="utf-8"
    )
    assert find_swap003(root) == []


# --- silent: the false-positive classes the fleet run turned up -----------


def test_a_module_dunder_is_not_a_symbol_of_another_package(tmp_path):
    """``getattr(torch, "__version__", "unknown")`` — a version probe on a
    statically imported optional dependency. Eleven of the first thirty-four
    fleet hits were exactly this, and none of them were defects."""
    source = (
        "import other_pkg\n"
        "\n"
        "def version():\n"
        "    return getattr(other_pkg, '__version__', 'unknown')\n"
    )
    assert hits(tmp_path, source) == []


def test_a_dotted_path_inside_a_code_template_is_data(tmp_path):
    """stapel-tools GENERATES these strings for scaffolded projects. A dotted
    path in a template is source text nobody hands to a resolver."""
    source = (
        'SETTINGS_TEMPLATE = """\n'
        "INSTALLED_APPS = ['other_pkg', 'django.contrib.admin']\n"
        "AUTH_USER_MODEL = 'other_pkg.User'\n"
        "from django.utils.module_loading import import_string\n"
        "import_string('other_pkg.validators.validate')\n"
        '"""\n'
    )
    assert hits(tmp_path, source, rel="stapel_thing/_templates.py") == []


def test_django_settings_strings_are_assignments_not_calls(tmp_path):
    source = (
        "AUTH_USER_MODEL = 'other_pkg.User'\n"
        "DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'\n"
        "MIDDLEWARE = ['other_pkg.middleware.Thing', 'corsheaders.middleware.CorsMiddleware']\n"
        "AUTHENTICATION_BACKENDS = ['other_pkg.backends.Backend']\n"
        "STORAGES = {'default': {'BACKEND': 'other_pkg.storage.Backend'}}\n"
    )
    assert hits(tmp_path, source, rel="stapel_thing/settings.py") == []


def test_migrations_are_not_scanned(tmp_path):
    source = (
        "from django.apps import apps\n"
        "x = apps.get_model('other_pkg', 'Profile')\n"
    )
    assert hits(tmp_path, source, rel="stapel_thing/migrations/0002_x.py") == []


def test_test_fixtures_may_resolve_fake_and_foreign_paths(tmp_path):
    source = (
        "from django.utils.module_loading import import_string\n"
        "x = import_string('other_pkg.validators.validate')\n"
    )
    assert hits(tmp_path, source, rel="tests/test_seam.py") == []
    assert hits(tmp_path, source, rel="stapel_thing/test_seam.py") == []


def test_a_vendored_checkout_is_another_distribution(tmp_path):
    """``stapel-studio/.vendor/`` carries whole sibling repos. Linting them as
    if they were this project's code re-reports every sibling's findings under
    the wrong distribution's name — four of the first thirty-four hits."""
    source = (
        "from django.utils.module_loading import import_string\n"
        "x = import_string('other_pkg.validators.validate')\n"
    )
    assert hits(tmp_path, source, rel=".vendor/stapel-other/checks.py") == []


def test_noqa_silences_a_deliberate_exception(tmp_path):
    source = (
        "from django.apps import apps\n"
        "x = apps.is_installed('other_pkg')  # noqa: SWAP003\n"
        "y = apps.is_installed('other_pkg')  # noqa\n"
    )
    assert hits(tmp_path, source) == []


def test_a_tree_with_no_packages_of_its_own_stays_silent(tmp_path):
    """Nothing here is 'ours', so every literal would read as foreign. A rule
    that flags a whole repo on the first run is a rule that gets deleted."""
    root = tmp_path / "loose"
    root.mkdir()
    (root / "script.py").write_text(
        "from django.utils.module_loading import import_string\n"
        "x = import_string('other_pkg.a.B')\n",
        encoding="utf-8",
    )
    assert find_swap003(root) == []


# --- wiring --------------------------------------------------------------


def test_swap003_reaches_the_combined_driver(tmp_path):
    """``lint_project`` is what ``stapel-verify`` composes, so a rule missing
    from it is a rule no consumer ever runs."""
    source = (
        "from django.utils.module_loading import import_string\n"
        "x = import_string('other_pkg.a.B')\n"
    )
    root = build(tmp_path, {"stapel_thing/services.py": source})
    assert "SWAP003" in {v.rule for v in lint_project(root)}
