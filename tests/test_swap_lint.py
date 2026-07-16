"""stapel-swap-lint tests (§55 extensibility-presenters.md — SWAP001/SWAP002).

SWAP001: a class named as the ``default=`` of a ``get_model``/``get_presenter``
call elsewhere in the tree is registered as swappable; any other file that
imports (or imports-and-instantiates) it directly is flagged. SWAP002: a
``views.py`` instantiating a ``dto.py``-sourced ``@dataclass`` directly,
bypassing a presenter.
"""
from pathlib import Path

from stapel_tools.swap_lint import find_swap001, find_swap002, lint_project


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _codes(violations):
    return sorted(v.rule for v in violations)


# --- SWAP001: flagged --------------------------------------------------------


def test_direct_import_of_swappable_default_is_flagged(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/presenters.py", """\
from stapel_core.django.swappable import get_presenter

PRESENTER_KEY = "USERS_PROFILE_PRESENTER"


class UserProfilePresenter:
    pass


def get_user_profile_presenter():
    return get_presenter(PRESENTER_KEY, default="app.presenters.UserProfilePresenter")
""")
    _write(proj, "app/views.py", """\
from app.presenters import UserProfilePresenter

presenter = UserProfilePresenter
""")
    violations = find_swap001(proj)
    assert "SWAP001" in _codes(violations)
    hit = [v for v in violations if v.path.endswith("views.py")][0]
    assert "UserProfilePresenter" in hit.message


def test_direct_instantiation_via_imported_swappable_is_flagged(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/models_swap.py", """\
from stapel_core.django.swappable import get_model


class Widget:
    pass


def get_widget():
    return get_model("APP_WIDGET", default="app.models_swap.Widget")
""")
    _write(proj, "app/views.py", """\
from app.models_swap import Widget

instance = Widget()
""")
    violations = find_swap001(proj)
    messages = [v.message for v in violations]
    assert any("direct instantiation" in m for m in messages)


def test_noqa_suppresses_swap001(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/presenters.py", """\
from stapel_core.django.swappable import get_presenter


class LeafPresenter:
    pass


def get_leaf_presenter():
    return get_presenter("LEAF", default="app.presenters.LeafPresenter")
""")
    _write(proj, "app/views.py", """\
from app.presenters import LeafPresenter  # noqa: SWAP001
""")
    assert find_swap001(proj) == []


# --- SWAP001: not flagged ----------------------------------------------------


def test_no_registry_no_findings(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/views.py", """\
from app.presenters import SomethingElse

x = SomethingElse()
""")
    assert find_swap001(proj) == []


def test_indirect_accessor_usage_is_clean(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/presenters.py", """\
from stapel_core.django.swappable import get_presenter


class UserProfilePresenter:
    pass


def get_user_profile_presenter():
    return get_presenter("USERS_PROFILE_PRESENTER", default="app.presenters.UserProfilePresenter")
""")
    _write(proj, "app/views.py", """\
from app.presenters import get_user_profile_presenter

presenter_cls = get_user_profile_presenter()
""")
    assert find_swap001(proj) == []


def test_tests_dir_is_excluded_from_swap001(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/presenters.py", """\
from stapel_core.django.swappable import get_presenter


class UserProfilePresenter:
    pass


def get_user_profile_presenter():
    return get_presenter("USERS_PROFILE_PRESENTER", default="app.presenters.UserProfilePresenter")
""")
    _write(proj, "tests/test_presenters.py", """\
from app.presenters import UserProfilePresenter

instance = UserProfilePresenter()
""")
    assert find_swap001(proj) == []


# --- SWAP002: flagged --------------------------------------------------------


def test_dto_direct_instantiation_in_views_is_flagged(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/dto.py", """\
from dataclasses import dataclass


@dataclass
class ProfileDTO:
    \"\"\"Profile DTO.\"\"\"
    id: str
""")
    _write(proj, "app/views.py", """\
from app.dto import ProfileDTO


def profile_view(request):
    return ProfileDTO(id="1")
""")
    violations = find_swap002(proj)
    assert _codes(violations) == ["SWAP002"]
    assert "ProfileDTO" in violations[0].message


def test_noqa_suppresses_swap002(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/dto.py", """\
from dataclasses import dataclass


@dataclass
class ProfileDTO:
    \"\"\"Profile DTO.\"\"\"
    id: str
""")
    _write(proj, "app/views.py", """\
from app.dto import ProfileDTO


def profile_view(request):
    return ProfileDTO(id="1")  # noqa: SWAP002
""")
    assert find_swap002(proj) == []


# --- SWAP002: not flagged ----------------------------------------------------


def test_dto_built_via_presenter_is_clean(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/dto.py", """\
from dataclasses import dataclass


@dataclass
class ProfileDTO:
    \"\"\"Profile DTO.\"\"\"
    id: str
""")
    _write(proj, "app/views.py", """\
from app.presenters import get_profile_presenter


def profile_view(request):
    presenter_cls = get_profile_presenter()
    return presenter_cls.present(request.user)
""")
    assert find_swap002(proj) == []


def test_locally_defined_dataclass_in_views_is_not_flagged(tmp_path):
    # Not sourced from dto.py -> out of scope for SWAP002 (no cross-module
    # presenter contract to bypass).
    proj = tmp_path / "proj"
    _write(proj, "app/views.py", """\
from dataclasses import dataclass


@dataclass
class LocalHelper:
    \"\"\"Local, view-only helper — not a DTO.\"\"\"
    id: str


def helper_view(request):
    return LocalHelper(id="1")
""")
    assert find_swap002(proj) == []


def test_tests_dir_is_excluded_from_swap002(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/dto.py", """\
from dataclasses import dataclass


@dataclass
class ProfileDTO:
    \"\"\"Profile DTO.\"\"\"
    id: str
""")
    _write(proj, "tests/test_views.py", """\
from app.dto import ProfileDTO


def test_something():
    dto = ProfileDTO(id="1")
    assert dto.id == "1"
""")
    assert find_swap002(proj) == []


def test_non_views_file_is_not_scanned_for_swap002(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/dto.py", """\
from dataclasses import dataclass


@dataclass
class ProfileDTO:
    \"\"\"Profile DTO.\"\"\"
    id: str
""")
    _write(proj, "app/serializers.py", """\
from app.dto import ProfileDTO

instance = ProfileDTO(id="1")
""")
    assert find_swap002(proj) == []


# --- lint_project combines both ----------------------------------------------


def test_lint_project_combines_swap001_and_swap002(tmp_path):
    proj = tmp_path / "proj"
    _write(proj, "app/presenters.py", """\
from stapel_core.django.swappable import get_presenter


class UserProfilePresenter:
    pass


def get_user_profile_presenter():
    return get_presenter("USERS_PROFILE_PRESENTER", default="app.presenters.UserProfilePresenter")
""")
    _write(proj, "app/dto.py", """\
from dataclasses import dataclass


@dataclass
class ProfileDTO:
    \"\"\"Profile DTO.\"\"\"
    id: str
""")
    _write(proj, "app/views.py", """\
from app.presenters import UserProfilePresenter
from app.dto import ProfileDTO


def profile_view(request):
    return ProfileDTO(id="1")
""")
    violations = lint_project(proj)
    assert _codes(violations) == ["SWAP001", "SWAP002"]
