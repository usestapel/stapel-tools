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
publishes its own owner name and subject types, in the three shapes
:mod:`stapel_gdpr.declarations` reads — the canonical
``register_gdpr_owner``, the in-process ``GDPRProvider.section``, and the
static ``OWNER``/``SUBJECT_TYPES`` constants. What cannot be read is a hard
failure naming the library: a guessed owner is a store nobody asks and nobody
waits for, which is silent retention with a receipt that says DELETED.

And the last class here is the one that decides whether any of the above is
true: a generated project is booted and graded by stapel-gdpr's OWN
``gdpr.E009``/``gdpr.E010``, the checks written for the 2026-09-07 fleet
incident. Reading "the same seams" is a claim about two files; running the
checks over the emitted map is the only thing that can refute it.
"""
import json
import re
import subprocess
import sys
from datetime import date

import pytest
from siblings import requires

from stapel_tools._gdpr_owners import (
    _seams,
    data_owners_version,
    derivation_table,
    derive_data_owners,
    inject_derived_data_owners,
    owner_declarations,
    read_owner_declaration,
    read_owner_declarations,
)
from stapel_tools._module_config import render_settings_block
from stapel_tools.adoption_lint import read_gdpr_owner
from stapel_tools.assemble_scaffold import assemble_scaffold
from stapel_tools.create_project import create_project
from stapel_tools.new_service import scaffold_service

ERASURE_SCHEMA = json.dumps({"type": "object"})

#: The seam vocabulary the generator reads WITH — stapel-gdpr's own when it is
#: installed. Asserting `via` against this rather than against a string of this
#: suite's own is the point: the label is the library's, not ours.
SEAMS = _seams()


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
        assert decl.via == SEAMS.constant

    def test_an_in_process_provider_owns_the_account(self, workspace):
        """A registered GDPRProvider predates entity subjects — it erases the
        account, and gdpr.E002 fires if it is absent from the map."""
        decl = read_owner_declaration("ledger", workspace)
        assert (decl.owner, decl.subject_types) == ("ledger", ("account",))
        assert decl.via == SEAMS.provider

    def test_the_owner_name_is_the_library_s_own_not_the_package_name(self, workspace):
        assert read_owner_declaration("blobstore", workspace).owner == "blobs"
        assert read_owner_declaration("atlas", workspace).owner == "cartography"

    def test_all_three_seams_of_one_library_are_one_owner(self, workspace):
        """A modern library is sighted three times over — canonical
        registration, provider, constants — and that is agreement. The seam
        recorded is the most authoritative one; the subject types come from
        whichever sighting carries them."""
        d = _lib(workspace, "beacon")
        (d / "erasure.py").write_text(
            "OWNER = 'beacon'\nSUBJECT_TYPES = ('account', 'signal')\n"
        )
        (d / "gdpr.py").write_text(
            "from stapel_core.gdpr import GDPRProvider\n\n\n"
            "class BeaconGDPRProvider(GDPRProvider):\n    section = 'beacon'\n"
        )
        (d / "apps.py").write_text(
            "from stapel_core.gdpr import gdpr_registry, register_gdpr_owner\n"
            "from .erasure import OWNER, SUBJECT_TYPES, erase_subject\n"
            "from .gdpr import BeaconGDPRProvider\n"
            "gdpr_registry.register(BeaconGDPRProvider())\n"
            "register_gdpr_owner(OWNER, SUBJECT_TYPES, erase_subject)\n"
        )
        declarations = read_owner_declarations("beacon", workspace)
        assert [d.owner for d in declarations] == ["beacon"]
        assert declarations[0].subject_types == ("account", "signal")
        assert declarations[0].via == SEAMS.registration

    def test_the_static_constants_are_read_without_the_consume_contract(
        self, workspace
    ):
        """stapel_gdpr.declarations reads OWNER/SUBJECT_TYPES off the installed
        package whether or not it also ships
        schemas/consumes/gdpr.erasure.requested.json. This reader used to
        require that file first, so a library without it kept its name (from
        the provider) and silently lost its subject types — gdpr.W012, emitted
        by the generator."""
        d = _lib(workspace, "sonar")
        (d / "erasure.py").write_text(
            "GDPR_OWNER = 'sonar'\nGDPR_SUBJECT_TYPES = ('account', 'ping')\n"
        )
        assert not (d / "schemas").exists()
        decl = read_owner_declaration("sonar", workspace)
        assert (decl.owner, decl.subject_types) == ("sonar", ("account", "ping"))
        assert decl.via == SEAMS.constant

    def test_a_library_whose_seams_name_two_owners_declares_both(self, workspace):
        """What the boot checks see is what must be listed: an unlisted second
        name is gdpr.E010, a store nothing is ever asked to erase."""
        d = _lib(workspace, "janus")
        (d / "erasure.py").write_text("OWNER = 'janus'\nSUBJECT_TYPES = ('account',)\n")
        (d / "gdpr.py").write_text(
            "from stapel_core.gdpr import GDPRProvider\n\n\n"
            "class JanusGDPRProvider(GDPRProvider):\n    section = 'janus_legacy'\n"
        )
        (d / "apps.py").write_text(
            "from stapel_core.gdpr import gdpr_registry\n"
            "from .gdpr import JanusGDPRProvider\n"
            "gdpr_registry.register(JanusGDPRProvider())\n"
        )
        assert [x.owner for x in read_owner_declarations("janus", workspace)] == [
            "janus", "janus_legacy",
        ]

    def test_the_canonical_registration_alone_is_participation(self, workspace):
        """A library on `register_gdpr_owner` with literal arguments and no
        constants at all: invisible to a reader that only knew the older two
        seams, which is gdpr.E010 in the generated project."""
        d = _lib(workspace, "pulsar")
        (d / "apps.py").write_text(
            "from stapel_core.gdpr import register_gdpr_owner\n"
            "register_gdpr_owner('pulsar', ('account', 'beam'), erase_subject)\n"
        )
        decl = read_owner_declaration("pulsar", workspace)
        assert (decl.owner, decl.subject_types) == ("pulsar", ("account", "beam"))
        assert decl.via == SEAMS.registration

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
        assert "ledger" in table and SEAMS.provider in table


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


#: Every one of them, through `siblings.requires` rather than a bare skipif.
#: The class used to carry `pytest.mark.skipif(not all(_importable(...)))`,
#: which is invisible to STAPEL_TEST_STRICT_SIBLINGS: `stapel_chat` was the
#: one member nobody had declared, so all four of these silently did not run
#: on any runner — and the drift they exist to catch (stapel-chat's CONFIG.MD
#: declaring a `settings` source the two-token contract has no room for, which
#: made `assemble_scaffold` raise for the whole fleet selection) sat in a
#: published release. A gate that cannot see the thing it guards is worse than
#: no gate, because it is believed.
#:
#: Each `@requires` below spells its modules out as LITERALS, and lists only
#: what that test actually reaches: `stapel-sibling-lint` reads them out of
#: the AST to check the `test` extra still describes the suite, and a name
#: assembled at runtime is invisible to it — which is a second way to lose
#: exactly what was lost here.


class TestAgainstTheRealFleet:
    @requires("stapel_core", "stapel_gdpr", "stapel_auth", "stapel_profiles",
              "stapel_cdn", "stapel_chat")
    def test_the_studio_call_shape_generates_without_a_config(self, tmp_path):
        """``assemble_scaffold(..., config=None)`` — studio_orchestrator's exact
        call, which used to die on 'required module settings are missing'."""
        result = assemble_scaffold(
            "derived", libs=FLEET_LIBS, output_dir=tmp_path, verify=False
        )
        settings = (result.project_dir / "config" / "settings.py").read_text()
        assert "STAPEL_GDPR = {" in settings
        assert '"DATA_OWNERS_VERSION"' in settings

    @requires("stapel_core", "stapel_gdpr", "stapel_auth", "stapel_profiles",
              "stapel_cdn", "stapel_chat")
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

    @requires("stapel_core", "stapel_auth", "stapel_profiles")
    def test_a_project_without_gdpr_emits_no_inventory(self, tmp_path):
        result = assemble_scaffold(
            "nogdpr", libs=["auth", "profiles"], output_dir=tmp_path, verify=False
        )
        settings = (result.project_dir / "config" / "settings.py").read_text()
        assert "STAPEL_GDPR" not in settings

    @requires("stapel_core", "stapel_gdpr", "stapel_auth", "stapel_chat")
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


# ---------------------------------------------------------------------------
# ...and stapel-gdpr's own checks grade the result
# ---------------------------------------------------------------------------

#: Booted inside the generated project. Reads the map the generator emitted and
#: runs the two errors that fire on an inventory the installed libraries
#: disagree with — `gdpr.E009` (a name nothing declares) and `gdpr.E010` (a
#: declared owner the host never lists). Both were written for the 2026-09-07
#: fleet incident and neither existed when this derivation did.
_GRADE_THE_INVENTORY = """
import json, os, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.conf import settings
from stapel_gdpr.checks import check_data_owner_names
from stapel_gdpr.declarations import installed_owner_declarations

payload = {
    "map": settings.STAPEL_GDPR["DATA_OWNERS"],
    "declared": sorted(installed_owner_declarations()),
    "problems": [[p.id, p.msg] for p in check_data_owner_names(None)],
}
sys.stdout.write("PARITY " + json.dumps(payload))
"""

#: The same, with the map replaced by the shape the fleet was actually
#: deployed with — app labels in a flat list. If this does NOT produce
#: gdpr.E009, the check is not running and the assertion above proves nothing.
_GRADE_THE_OLD_SHAPE = _GRADE_THE_INVENTORY.replace(
    "from stapel_gdpr.checks import check_data_owner_names",
    'settings.STAPEL_GDPR["DATA_OWNERS"] = ["auth", "profiles"]\n'
    "from stapel_gdpr.checks import check_data_owner_names",
)


def _grade(project_dir, script):
    """Boot the generated project and return its check report."""
    import os as _os

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_dir, capture_output=True, text=True,
        env={**_os.environ, "DJANGO_SETTINGS_MODULE": "config.settings"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    marker = "PARITY "
    assert marker in result.stdout, result.stdout + result.stderr
    return json.loads(result.stdout.split(marker, 1)[1])


class TestTheLibrariesGradeTheGeneratedInventory:
    """The parity gate: the generator's map, judged by the checks that read
    the installed libraries at boot.

    Not a skipif — every sibling here is declared in the `test` extra, and
    `stapel-gdpr>=0.5.4` is the floor that carries `stapel_gdpr.declarations`
    and these two check ids. On a runner without them the subprocess fails
    loudly rather than the test passing quietly.
    """

    @requires("stapel_core", "stapel_gdpr", "stapel_auth", "stapel_profiles",
              "stapel_cdn", "stapel_chat")
    def test_the_generated_map_raises_no_e009_and_no_e010(self, tmp_path):
        result = assemble_scaffold(
            "graded", libs=FLEET_LIBS, output_dir=tmp_path, verify=False
        )
        report = _grade(result.project_dir, _GRADE_THE_INVENTORY)
        assert [p[0] for p in report["problems"]] == [], report["problems"]
        # …and the grading was real: every library that declares an owner is
        # visible to the checks, and the emitted map is exactly that set.
        assert report["declared"], "no owner library visible — nothing was graded"
        assert sorted(report["map"]) == report["declared"]

    @requires("stapel_core", "stapel_gdpr", "stapel_auth", "stapel_profiles",
              "stapel_cdn", "stapel_chat")
    def test_the_shape_this_replaces_still_fails_the_same_checks(self, tmp_path):
        """The gate has to bite. `["auth", "profiles"]` is what the fleet ran:
        an app label nothing declares (gdpr.E009 -> "profile") and two stores
        the inventory never names at all (gdpr.E010)."""
        result = assemble_scaffold(
            "ungraded", libs=FLEET_LIBS, output_dir=tmp_path, verify=False
        )
        report = _grade(result.project_dir, _GRADE_THE_OLD_SHAPE)
        ids = {p[0] for p in report["problems"]}
        assert "gdpr.E009" in ids and "gdpr.E010" in ids, report["problems"]
        e009 = next(msg for pid, msg in report["problems"] if pid == "gdpr.E009")
        assert '"profiles" -> "profile"' in e009

    @requires("stapel_core", "stapel_gdpr", "stapel_auth", "stapel_profiles",
              "stapel_cdn", "stapel_chat")
    def test_every_name_the_generator_emits_is_one_a_library_declares(self):
        """Names, straight from the two readers, with no project in between —
        `_gdpr_owners` reads the source tree, `stapel_gdpr.declarations` reads
        the seam vocabulary. The app labels are what must NOT appear."""
        derived = derive_data_owners(FLEET_LIBS)
        assert {"profiles", "cdn"}.isdisjoint(derived), derived
        assert {"profile", "media", "auth", "chat"} <= set(derived), derived
