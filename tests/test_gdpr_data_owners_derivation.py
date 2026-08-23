"""The gdpr inventory is derived from the selection, never asked for.

``STAPEL_GDPR["DATA_OWNERS"]`` lists every store an erasure is certified
against. stapel-gdpr refuses to boot without it (``gdpr.E001``) and refuses to
boot when a store already wired in is missing from it (``gdpr.E002``) — so a
generator that installs the libraries and emits no map produces a project that
is dead on arrival, and this repo's ``check_required_settings`` refused to
generate one at all. That refusal is where stapel-studio's scaffold-assembly
task stopped: it calls ``assemble_scaffold(..., config=None)`` and has no map
to hand over.

The map was never the caller's to supply. Every participating library
publishes its own owner name and subject types, in two shapes that are both
read here — the erasure-request contract (ADO005's detection, imported, not
forked) and the in-process ``GDPRProvider``. What cannot be read is a hard
failure naming the library: a guessed owner is a store nobody asks and nobody
waits for, which is silent retention with a receipt that says DELETED.
"""
import importlib.util
import json
import re
from datetime import date

import pytest

from stapel_tools._gdpr_owners import (
    data_owners_version,
    derivation_table,
    derive_data_owners,
    inject_derived_data_owners,
    owner_declarations,
    read_owner_declaration,
)
from stapel_tools._module_config import render_settings_block
from stapel_tools.adoption_lint import read_gdpr_owner
from stapel_tools.assemble_scaffold import assemble_scaffold
from stapel_tools.create_project import create_project
from stapel_tools.new_service import scaffold_service

ERASURE_SCHEMA = json.dumps({"type": "object"})


def _importable(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _lib(root, name):
    d = root / f"stapel-{name}"
    d.mkdir(parents=True)
    return d


def _erasure_owner(root, name, *, owner, subjects, readable=True):
    """A library on the erasure-request protocol: it ships the consume
    contract, which IS its declaration of participation (ADO005)."""
    d = _lib(root, name)
    consumes = d / "schemas" / "consumes"
    consumes.mkdir(parents=True)
    (consumes / "gdpr.erasure.requested.json").write_text(ERASURE_SCHEMA)
    if readable:
        (d / "erasure.py").write_text(
            f"OWNER = {owner!r}\nSUBJECT_TYPES = {tuple(subjects)!r}\n"
        )
    else:
        # Participation is undeniable, the name is not readable — the case the
        # generator must refuse rather than guess.
        (d / "erasure.py").write_text("OWNER = _lookup_owner_name()\n")
    return d


def _provider_owner(root, name, *, section, cls=None, register=True):
    """A library on the older in-process registry: apps.py registers its own
    GDPRProvider subclass, and the class carries the section."""
    d = _lib(root, name)
    cls = cls or f"{name.capitalize()}GDPRProvider"
    (d / "gdpr.py").write_text(
        "from stapel_core.gdpr import GDPRProvider\n\n\n"
        f"class {cls}(GDPRProvider):\n    section = {section!r}\n"
    )
    if register:
        (d / "apps.py").write_text(
            "from stapel_core.gdpr import gdpr_registry\n\n"
            f"from .gdpr import {cls}\n\n"
            f"gdpr_registry.register({cls}())\n"
        )
    return d


@pytest.fixture
def workspace(tmp_path_factory):
    """A fixture fleet with one library of every shape that matters.

    Deliberately synthetic names: the reader prefers the INSTALLED
    distribution, so a fixture that reused a real library's name would be
    reading the real one and proving nothing.
    """
    root = tmp_path_factory.mktemp("gdpr_owner_workspace")
    _erasure_owner(root, "vaults", owner="vaults", subjects=["account", "vault"])
    # The name a library answers to is its own to choose, and it is not the
    # package name: stapel-cdn answers to 'media', stapel-profiles to 'profile'.
    _erasure_owner(root, "blobstore", owner="blobs", subjects=["account", "file"])
    _provider_owner(root, "ledger", section="ledger")
    _provider_owner(root, "atlas", section="cartography")
    _lib(root, "widgets")  # holds no personal data at all
    return root


# ---------------------------------------------------------------------------
# reading one library
# ---------------------------------------------------------------------------


class TestOneLibrary:
    def test_the_erasure_contract_carries_the_name_and_the_subjects(self, workspace):
        decl = read_owner_declaration("vaults", workspace)
        assert (decl.owner, decl.subject_types) == ("vaults", ("account", "vault"))
        assert decl.via == "erasure protocol"

    def test_an_in_process_provider_owns_the_account(self, workspace):
        """A registered GDPRProvider predates entity subjects — it erases the
        account, and gdpr.E002 fires if it is absent from the map."""
        decl = read_owner_declaration("ledger", workspace)
        assert (decl.owner, decl.subject_types) == ("ledger", ("account",))
        assert decl.via == "in-process provider"

    def test_the_owner_name_is_the_library_s_own_not_the_package_name(self, workspace):
        assert read_owner_declaration("blobstore", workspace).owner == "blobs"
        assert read_owner_declaration("atlas", workspace).owner == "cartography"

    def test_a_library_that_holds_no_personal_data_is_not_an_owner(self, workspace):
        assert read_owner_declaration("widgets", workspace) is None

    def test_a_library_that_is_not_there_at_all_is_not_an_owner(self, workspace):
        assert read_owner_declaration("nosuchlib", workspace) is None

    def test_the_gdpr_host_owns_no_store_of_its_own(self, workspace):
        """stapel-gdpr registers whatever ``GDPR_PROVIDERS`` names — a variable,
        not a class of its own. Listing the host as its own data owner would
        declare a store that does not exist."""
        _provider_owner(workspace, "gdpr", section="gdpr")
        assert read_owner_declaration("gdpr", workspace) is None

    def test_registering_a_class_it_does_not_define_is_not_participation(
        self, workspace
    ):
        d = _lib(workspace, "relay")
        (d / "apps.py").write_text(
            "from stapel_core.gdpr import gdpr_registry\n"
            "gdpr_registry.register(provider_cls())\n"
        )
        assert read_owner_declaration("relay", workspace) is None


# ---------------------------------------------------------------------------
# the refusal
# ---------------------------------------------------------------------------


class TestUnreadableDeclaration:
    def test_a_participating_library_with_no_readable_name_fails_by_name(
        self, workspace
    ):
        _erasure_owner(workspace, "opaque", owner=None, subjects=[], readable=False)
        with pytest.raises(SystemExit) as exc:
            derive_data_owners(["opaque"], workspace)
        message = str(exc.value)
        assert "stapel-opaque" in message, "the failure names the library"
        assert "gdpr.erasure.requested.json" in message, "…and why it counts as an owner"
        assert "never asked" in message, "…and what a missing owner costs"

    def test_a_provider_whose_section_is_unreadable_fails_by_name(self, workspace):
        d = _lib(workspace, "murk")
        (d / "gdpr.py").write_text(
            "from stapel_core.gdpr import GDPRProvider\n\n\n"
            "class MurkGDPRProvider(GDPRProvider):\n"
            "    section = _section_from_env()\n"
        )
        (d / "apps.py").write_text(
            "from stapel_core.gdpr import gdpr_registry\n"
            "from .gdpr import MurkGDPRProvider\n"
            "gdpr_registry.register(MurkGDPRProvider())\n"
        )
        with pytest.raises(SystemExit) as exc:
            derive_data_owners(["murk"], workspace)
        assert "stapel-murk" in str(exc.value)
        assert "gdpr.E002" in str(exc.value)

    def test_the_refusal_never_offers_a_placeholder_to_paste(self, workspace):
        _erasure_owner(workspace, "opaque", owner=None, subjects=[], readable=False)
        with pytest.raises(SystemExit) as exc:
            derive_data_owners(["opaque"], workspace)
        message = str(exc.value)
        assert "example" not in message.lower()
        assert "STAPEL_GDPR = {" not in message


# ---------------------------------------------------------------------------
# the map
# ---------------------------------------------------------------------------


class TestDerivedMap:
    def test_the_map_is_exactly_the_selected_owners(self, workspace):
        assert derive_data_owners(
            ["core", "gdpr", "vaults", "ledger", "widgets"], workspace
        ) == {
            "ledger": ["account"],
            "vaults": ["account", "vault"],
        }

    def test_a_library_that_is_not_selected_is_not_in_the_map(self, workspace):
        assert "blobs" not in derive_data_owners(["gdpr", "vaults"], workspace)

    def test_the_version_is_the_generation_date(self):
        assert data_owners_version(date(2026, 1, 31)) == "2026-01-31.1"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.1", data_owners_version())

    def test_the_derivation_table_says_where_every_name_came_from(self, workspace):
        table = derivation_table(owner_declarations(["vaults", "ledger"], workspace))
        assert "vaults" in table and "stapel-vaults/erasure.py" in table
        assert "ledger" in table and "in-process provider" in table


class TestInjection:
    def test_a_project_without_gdpr_gets_no_map(self, workspace):
        assert inject_derived_data_owners(None, ["vaults", "ledger"], workspace_root=workspace) is None

    def test_a_project_with_gdpr_gets_the_map_and_a_version(self, workspace):
        config = inject_derived_data_owners(
            None, ["gdpr", "vaults"], workspace_root=workspace, verbose=False
        )
        assert config["gdpr"]["DATA_OWNERS"] == {"vaults": ["account", "vault"]}
        assert config["gdpr"]["DATA_OWNERS_VERSION"] == data_owners_version()

    def test_a_hand_written_inventory_is_never_overwritten(self, workspace):
        """An operator who wrote the map knows about stores this generator
        cannot see — a search index, a warehouse, a third-party processor."""
        supplied = {"gdpr": {
            "DATA_OWNERS": {"vaults": ["account"], "warehouse": ["account"]},
            "DATA_OWNERS_VERSION": "2026-01-01.7",
        }}
        assert inject_derived_data_owners(
            supplied, ["gdpr", "vaults", "ledger"], workspace_root=workspace
        ) == supplied

    def test_nothing_is_invented_when_no_selected_library_owns_a_store(
        self, workspace
    ):
        """gdpr alone owns nothing, and an empty map is exactly the state
        gdpr.E001 fires on — so leave it unset and let the required-settings
        gate say so, with the fix attached."""
        assert inject_derived_data_owners(
            None, ["gdpr", "widgets"], workspace_root=workspace
        ) is None


class TestRenderedSettings:
    def test_the_block_carries_the_law_the_map_answers_to(self, workspace):
        config = inject_derived_data_owners(
            None, ["gdpr", "vaults", "ledger"], workspace_root=workspace, verbose=False
        )
        block = render_settings_block(config)
        assert "STAPEL_GDPR = {" in block
        assert '"DATA_OWNERS":' in block
        assert "never asked and never waited for" in block, "the comment states the law"
        assert "Bump it whenever DATA_OWNERS changes" in block

    def test_a_wide_map_is_rendered_one_owner_per_line(self, workspace):
        wide = {"gdpr": {"DATA_OWNERS": {
            f"owner{i}": ["account", "workspace", "recording"] for i in range(8)
        }}}
        block = render_settings_block(wide)
        assert '"DATA_OWNERS": {\n' in block
        assert "        'owner0': ['account', 'workspace', 'recording'],\n" in block


# ---------------------------------------------------------------------------
# ...and it is wired into generation, against the real fleet
# ---------------------------------------------------------------------------

#: The studio-shaped selection: the gdpr host plus libraries of both
#: participation shapes (auth/profiles/cdn on the erasure protocol, chat on the
#: in-process registry) — the one that used to be refused for want of a map.
FLEET_LIBS = ["gdpr", "auth", "profiles", "cdn", "chat"]


@pytest.mark.skipif(
    not all(_importable(f"stapel_{lib}") for lib in [*FLEET_LIBS, "core"]),
    reason="requires the fleet libraries importable (true in the shared workspace "
    "venv; not installed for a bare stapel-tools checkout)",
)
class TestAgainstTheRealFleet:
    def test_the_studio_call_shape_generates_without_a_config(self, tmp_path):
        """``assemble_scaffold(..., config=None)`` — studio_orchestrator's exact
        call, which used to die on 'required module settings are missing'."""
        result = assemble_scaffold(
            "derived", libs=FLEET_LIBS, output_dir=tmp_path, verify=False
        )
        settings = (result.project_dir / "config" / "settings.py").read_text()
        assert "STAPEL_GDPR = {" in settings
        assert '"DATA_OWNERS_VERSION"' in settings

    def test_the_map_matches_what_each_library_declares(self, tmp_path):
        derived = derive_data_owners(FLEET_LIBS)
        # The erasure-protocol half, read straight from the libraries.
        for lib in ["auth", "profiles", "cdn"]:
            decl = read_gdpr_owner(f"stapel_{lib}", [])
            assert derived[decl.owner] == list(decl.subject_types)
        # …and the in-process half, whose section gdpr.E002 checks against.
        assert derived["chat"] == ["account"]
        # Names are the libraries' own, not their package names.
        assert "cdn" not in derived and "media" in derived
        assert "profiles" not in derived and "profile" in derived

    def test_a_project_without_gdpr_emits_no_inventory(self, tmp_path):
        result = assemble_scaffold(
            "nogdpr", libs=["auth", "profiles"], output_dir=tmp_path, verify=False
        )
        settings = (result.project_dir / "config" / "settings.py").read_text()
        assert "STAPEL_GDPR" not in settings

    def test_a_fleet_service_gets_the_inventory_of_its_own_apps(self, tmp_path):
        """A service is its own deployment: the map lists the stores THAT
        service installs, not the fleet's."""
        create_project(
            name="fleet", project_type="microservices", title="Fleet",
            url="https://fleet.test", company_name="Fleet Co",
            company_email="hi@fleet.test", modules=["core"],
            output_dir=tmp_path, use_submodules=False, init_git=False,
        )
        scaffold_service(
            slug="privacy", title="Privacy", prefix="svc-",
            project_root=tmp_path / "fleet",
            stapel_apps=["stapel_gdpr", "stapel_auth", "stapel_chat"],
        )
        settings = (
            tmp_path / "fleet" / "svc-privacy" / "config" / "settings" / "base.py"
        ).read_text()
        assert "STAPEL_GDPR = {" in settings
        assert "'auth': ['account']" in settings
        assert "'chat': ['account']" in settings
        assert "'profile'" not in settings, "stapel_profiles is not in this service"
