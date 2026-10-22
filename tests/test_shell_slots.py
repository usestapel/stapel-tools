"""The static slot assembly (composition spec §4) and its inversion checks
2-5 (§5): what must fail, fails with the hint the caller can act on."""
import pytest

from stapel_tools._shell_slots import parse_slot_args, plan_slots
from stapel_tools._shell_templates import SHELL_TEMPLATES
from stapel_tools.create_project import FRONTEND_REACT_LIBS, create_project

BELL = FRONTEND_REACT_LIBS["notifications"]["embeds"][0]
SWITCHER = FRONTEND_REACT_LIBS["translate"]["embeds"][0]


def _pair(package, *embeds):
    return {"package": package, "embeds": list(embeds)}


def _search(eid, order=10):
    return {
        "id": eid, "slot": "topbar.center", "order": order,
        "component": {"export": "SearchBox", "subpath": "default"},
    }


PAIRS = [
    _pair("@stapel/notifications-react", BELL),
    _pair("@stapel/translate-react", SWITCHER),
]


class TestTemplate:
    def test_unknown_template_lists_the_keys(self):
        with pytest.raises(ValueError) as exc:
            plan_slots("header-only", PAIRS, auth_gated=True)
        for key in SHELL_TEMPLATES:
            assert key in str(exc.value)

    def test_queued_template_names_the_queue_item(self):
        """Inversion check 5."""
        with pytest.raises(ValueError, match="queued"):
            plan_slots("sidenav-only", PAIRS, auth_gated=True)

    def test_topbar_only_is_refused_for_the_monolith_naming_its_queue_item(self):
        with pytest.raises(ValueError, match="monolith --shell topbar-only"):
            plan_slots("topbar-only", PAIRS, auth_gated=True)


class TestOverrides:
    def test_unknown_slot_lists_the_templates_slots(self):
        """Inversion check 2."""
        with pytest.raises(ValueError) as exc:
            plan_slots(
                "topbar-sidenav", PAIRS, auth_gated=True,
                overrides={"header.right": "notifications.bell"},
            )
        message = str(exc.value)
        assert "header.right" in message
        for slot in SHELL_TEMPLATES["topbar-sidenav"]["slots"]:
            assert slot in message

    def test_unknown_embed_lists_the_offered_ids(self):
        """Inversion check 3."""
        with pytest.raises(ValueError) as exc:
            plan_slots(
                "topbar-sidenav", PAIRS, auth_gated=True,
                overrides={"topbar.end": "bogus.embed"},
            )
        message = str(exc.value)
        assert "bogus.embed" in message
        assert "notifications.bell" in message
        assert "translate.language_switcher" in message

    def test_none_empties_the_slot(self):
        plan = plan_slots(
            "topbar-sidenav", PAIRS, auth_gated=True, overrides={"topbar.end": "none"},
        )
        assert plan.slots["topbar.end"] == []
        assert plan.filled == {}
        assert plan.overrides == {"topbar.end": "none"}

    def test_choosing_one_embed_keeps_only_it(self):
        plan = plan_slots(
            "topbar-sidenav", PAIRS, auth_gated=True,
            overrides={"topbar.end": "translate.language_switcher"},
        )
        assert [e["id"] for e in plan.slots["topbar.end"]] == ["translate.language_switcher"]


class TestConflicts:
    def test_two_embeds_on_a_single_capacity_slot_need_a_choice(self):
        """Inversion check 4 — never a silent first-wins."""
        pairs = [_pair("@stapel/a-react", _search("a.box")), _pair("@stapel/b-react", _search("b.box"))]
        with pytest.raises(ValueError) as exc:
            plan_slots("topbar-sidenav", pairs, auth_gated=True)
        assert str(exc.value) == (
            "two embeds claim topbar.center: a.box, b.box — pick one with "
            "--slot topbar.center=<id> or silence it with --slot topbar.center=none"
        )

    def test_the_choice_resolves_the_conflict(self):
        pairs = [_pair("@stapel/a-react", _search("a.box")), _pair("@stapel/b-react", _search("b.box"))]
        plan = plan_slots(
            "topbar-sidenav", pairs, auth_gated=True, overrides={"topbar.center": "b.box"},
        )
        assert [e["id"] for e in plan.slots["topbar.center"]] == ["b.box"]


class TestOfferAndOrder:
    def test_member_embeds_drop_without_a_gated_shell_with_a_note(self):
        plan = plan_slots("topbar-sidenav", PAIRS, auth_gated=False)
        assert [e["id"] for e in plan.slots["topbar.end"]] == ["translate.language_switcher"]
        assert plan.dropped == ["notifications.bell"]
        assert any("notifications.bell" in n for n in plan.notes)

    def test_embed_for_a_slot_the_template_lacks_is_dropped_with_a_note(self):
        stray = {**SWITCHER, "id": "translate.footer", "slot": "footer"}
        plan = plan_slots("topbar-sidenav", [_pair("@stapel/translate-react", stray)], auth_gated=True)
        assert plan.filled == {}
        assert plan.notes == ["embed translate.footer wants footer, which topbar-sidenav has no slot for"]

    def test_order_is_deterministic_by_order_then_id(self):
        embeds = [
            {**SWITCHER, "id": "z.last", "order": 10},
            {**SWITCHER, "id": "a.first", "order": 10},
            {**SWITCHER, "id": "m.early", "order": 5},
        ]
        forward = plan_slots("topbar-sidenav", [_pair("@stapel/x-react", *embeds)], auth_gated=True)
        backward = plan_slots(
            "topbar-sidenav", [_pair("@stapel/x-react", *reversed(embeds))], auth_gated=True,
        )
        ids = [e["id"] for e in forward.slots["topbar.end"]]
        assert ids == ["m.early", "a.first", "z.last"]
        assert ids == [e["id"] for e in backward.slots["topbar.end"]]

    def test_registry_order_puts_the_bell_before_the_switcher(self):
        plan = plan_slots("topbar-sidenav", list(reversed(PAIRS)), auth_gated=True)
        assert [e["id"] for e in plan.slots["topbar.end"]] == [
            "notifications.bell", "translate.language_switcher",
        ]
        assert plan.slots["topbar.end"][0]["_package"] == "@stapel/notifications-react"


class TestParseSlotArgs:
    def test_pairs(self):
        assert parse_slot_args(["topbar.end=none", "topbar.center=a.box"]) == {
            "topbar.end": "none", "topbar.center": "a.box",
        }

    @pytest.mark.parametrize("raw", ["topbar.end", "=none", "topbar.end="])
    def test_malformed(self, raw):
        with pytest.raises(ValueError, match="SLOT=EMBED"):
            parse_slot_args([raw])


def _create(tmp_path, **kw):
    create_project(
        name="app", project_type=kw.pop("project_type", "monolith"), title="App",
        url="https://app.example", company_name="Acme",
        company_email="hi@app.example", modules=kw.pop("modules", ["core"]),
        output_dir=tmp_path, use_submodules=False, init_git=False, **kw,
    )
    return tmp_path / "app"


class TestScaffoldExits:
    """The scaffold prints `Error: ...` and exits 1 before writing anything."""

    @pytest.mark.parametrize("kw, fragment", [
        ({"slots": {"header.right": "notifications.bell"}}, "topbar.start"),
        ({"slots": {"topbar.end": "bogus.embed"}}, "notifications.bell"),
        ({"shell": "sidenav-only"}, "queued"),
        ({"shell": "topbar-only"}, "monolith --shell topbar-only"),
    ])
    def test_exits_one_with_the_hint(self, tmp_path, capsys, kw, fragment):
        with pytest.raises(SystemExit) as exc:
            _create(tmp_path, modules=["core", "auth", "notifications"], **kw)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("Error: ") and fragment in err
        assert not (tmp_path / "app").exists()

    def test_shell_on_a_project_without_a_frontend_is_an_error(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            _create(tmp_path, project_type="minimal", shell="topbar-sidenav")
        assert "--shell/--slot" in capsys.readouterr().err


ACCEPTANCE = ["core", "auth", "billing", "workspaces", "notifications", "translate", "gdpr", "cdn"]


class TestEmission:
    def test_acceptance_selection_fills_topbar_end(self, tmp_path):
        import json

        import stapel_tools._frontend_templates as F

        front = _create(tmp_path, modules=ACCEPTANCE, shell="topbar-sidenav") / "frontend"
        embeds = (front / "src" / "embeds.generated.tsx").read_text()
        assert "export function TopbarEnd(): ReactElement" in embeds
        assert 'import { NotificationBell } from "@stapel/notifications-react/default";' in embeds
        assert 'import { LanguageSwitcher } from "@stapel/translate-react/default";' in embeds
        assert "<LanguageSwitcher compact={true} />" in embeds
        assert 'void navigate("/app/notifications");' in embeds
        assert 'data-testid="embed-notifications.bell"' in embeds
        assert embeds.index("embed-notifications.bell") < embeds.index("embed-translate.language_switcher")

        routes = (front / "src" / "routes.tsx").read_text()
        assert 'import { TopbarEnd, TopbarStart } from "./embeds.generated.js";' in routes
        assert (
            'slots={{ "topbar.start": <TopbarStart />, "topbar.end": <TopbarEnd /> }}'
            in routes
        )

        composition = json.loads((front / "stapel.composition.json").read_text())
        assert composition["shell"] == {
            "template": "topbar-sidenav",
            "slots": {
                "topbar.start": ["workspaces.switcher"],
                "topbar.end": [
                    "notifications.bell", "translate.language_switcher", "auth.account_menu",
                ],
            },
            "overrides": {},
            "dropped": [],
        }
        assert composition["auth"] == {"model": "public", "gated": True}
        assert composition["theme"] == "default"
        assert "notifications" in composition["modules"]

        gate = (front / "src" / "__tests__" / "nav.test.ts").read_text()
        assert gate == F.NAV_GATE_EMBEDS_TEST_TS
        assert 'import * as embeds from "../embeds.generated.js";' in gate

    def test_no_literal_text_in_generated_jsx(self, tmp_path):
        """`stapel/no-hardcoded-text`: the only text between tags is markup."""
        import re

        front = _create(tmp_path, modules=ACCEPTANCE) / "frontend"
        embeds = (front / "src" / "embeds.generated.tsx").read_text()
        assert re.findall(r">\s*[A-Za-z][^<>{}]*<", embeds) == []

    def test_selection_without_embeds_keeps_the_container_byte_identical(self, tmp_path):
        import stapel_tools._frontend_templates as F

        modules = ["core", "billing", "gdpr"]
        front = _create(tmp_path, modules=modules) / "frontend"
        assert not (front / "src" / "embeds.generated.tsx").exists()
        routes = (front / "src" / "routes.tsx").read_text()
        assert "slots=" not in routes and "embeds.generated" not in routes
        assert (front / "src" / "__tests__" / "nav.test.ts").read_text() == F.NAV_GATE_TEST_TS

        from stapel_tools.create_project import _frontend_react_entries

        entries = _frontend_react_entries(modules[1:])
        nav_pairs = F.nav_wired_pairs(entries, auth_wired=False)
        expected = F.render_routes_tsx(
            F.build_nav_route_plan(nav_pairs), auth_wired=False, want_landing=False,
            app_route_present=True, has_cdn=False, pairs=tuple(e["key"] for e in entries),
        )
        assert routes == expected

        from stapel_tools.create_project import _frontend_react_entries as entries_for

        assert not any(e.get("embeds") for e in entries_for(modules[1:]))

    def test_slot_none_emits_nothing(self, tmp_path):
        import json

        silence = {"topbar.start": "none", "topbar.end": "none"}
        front = _create(tmp_path, modules=ACCEPTANCE, slots=silence) / "frontend"
        assert not (front / "src" / "embeds.generated.tsx").exists()
        assert "slots=" not in (front / "src" / "routes.tsx").read_text()
        composition = json.loads((front / "stapel.composition.json").read_text())
        assert composition["shell"]["slots"] == {}
        assert composition["shell"]["overrides"] == silence

    def test_member_bell_dropped_without_auth(self, tmp_path, capsys):
        import json

        front = _create(
            tmp_path, modules=["core", "notifications", "translate"], auth_model="none",
        ) / "frontend"
        embeds = (front / "src" / "embeds.generated.tsx").read_text()
        assert "NotificationBell" not in embeds and "LanguageSwitcher" in embeds
        assert "useNavigate" not in embeds
        composition = json.loads((front / "stapel.composition.json").read_text())
        assert composition["shell"]["dropped"] == ["notifications.bell"]
        assert composition["auth"]["gated"] is False
        assert "note: embed notifications.bell is member-only" in capsys.readouterr().out

    def test_below_the_slots_floor_nothing_is_emitted(self, tmp_path, monkeypatch):
        import stapel_tools.create_project as cp

        monkeypatch.setattr(cp, "FRONTEND_SHELL_REACT_VERSION", "0.19.1")
        assert not cp.shell_has_slots()
        front = _create(tmp_path, modules=ACCEPTANCE) / "frontend"
        assert not (front / "src" / "embeds.generated.tsx").exists()
        assert "slots=" not in (front / "src" / "routes.tsx").read_text()


class TestManifestAssembly:
    """One scaffold call, three pairs, no hand edits: every slot's contents
    are the pairs' own `embeds` declarations (the registry mirror of each
    `nav-manifest.json`, drift-gated by scripts/check_nav_manifest_sync.py)."""

    MODULES = ["core", "auth", "workspaces", "notifications"]

    def _expected_slots(self):
        from stapel_tools.create_project import FRONTEND_REACT_LIBS

        declared: dict[str, list[dict]] = {}
        for key in ("auth", "workspaces", "notifications"):
            for embed in FRONTEND_REACT_LIBS[key]["embeds"]:
                declared.setdefault(embed["slot"], []).append(embed)
        return {
            slot: [e["id"] for e in sorted(embeds, key=lambda e: (e["order"], e["id"]))]
            for slot, embeds in declared.items()
        }

    def test_slots_come_from_the_manifests(self, tmp_path):
        import json

        front = _create(tmp_path, modules=self.MODULES, shell="topbar-sidenav") / "frontend"
        composition = json.loads((front / "stapel.composition.json").read_text())
        expected = self._expected_slots()
        assert composition["shell"]["slots"] == expected
        # Pinned too, so a mirror edit that empties a pair cannot pass by
        # agreeing with itself.
        assert expected == {
            "topbar.start": ["workspaces.switcher"],
            "topbar.end": ["notifications.bell", "auth.account_menu"],
        }

        embeds = (front / "src" / "embeds.generated.tsx").read_text()
        assert 'import { AccountMenu } from "@stapel/auth-react/default";' in embeds
        assert 'import { WorkspaceSwitcher } from "@stapel/workspaces-react/default";' in embeds
        assert "export function TopbarStart(): ReactElement" in embeds
        # account.root is routed (workspaces hangs its screens from it), so the
        # menu's `opens` resolves and it gets a working "Account" item.
        assert 'void navigate("/app/account");' in embeds
        assert 'void navigate("/app/notifications");' in embeds
        assert embeds.index("embed-notifications.bell") < embeds.index("embed-auth.account_menu")

    def test_the_selection_provider_wraps_the_member_shell(self, tmp_path):
        front = _create(tmp_path, modules=self.MODULES) / "frontend"
        routes = (front / "src" / "routes.tsx").read_text()
        assert 'import { WorkspaceSelectionProvider } from "@stapel/workspaces-react";' in routes
        assert (
            "<ProtectedRoute>\n"
            "        <WorkspaceSelectionProvider><AppChrome /></WorkspaceSelectionProvider>\n"
            "      </ProtectedRoute>"
        ) in routes

    def test_without_workspaces_no_selection_provider(self, tmp_path):
        front = _create(tmp_path, modules=["core", "auth", "notifications"]) / "frontend"
        routes = (front / "src" / "routes.tsx").read_text()
        assert "WorkspaceSelectionProvider" not in routes
        embeds = (front / "src" / "embeds.generated.tsx").read_text()
        # No account section is routed here: the menu mounts without onOpen
        # (sign-out only) rather than with a dead link.
        assert "<AccountMenu />" in embeds and "/app/account" not in embeds

    def test_offer_lists_what_the_intake_asks_about(self):
        from stapel_tools._shell_slots import offer_slots
        from stapel_tools.create_project import _frontend_react_entries

        offer = offer_slots(
            "topbar-sidenav", _frontend_react_entries(["auth", "workspaces", "notifications"]),
            auth_gated=True,
        )
        assert [e["id"] for e in offer.embeds] == [
            "workspaces.switcher", "notifications.bell", "auth.account_menu",
        ]
        assert offer.conflicts == {}
        assert offer.exclusive == ["topbar.center"]

    def test_offer_names_a_contested_exclusive_slot(self):
        from stapel_tools._shell_slots import offer_slots

        pairs = [
            {"package": "@x/a", "embeds": [
                {"id": "a.search", "slot": "topbar.center", "order": 1,
                 "component": {"export": "A", "subpath": "default"}}]},
            {"package": "@x/b", "embeds": [
                {"id": "b.search", "slot": "topbar.center", "order": 2,
                 "component": {"export": "B", "subpath": "default"}}]},
        ]
        offer = offer_slots("topbar-sidenav", pairs, auth_gated=True)
        assert offer.conflicts == {"topbar.center": ["a.search", "b.search"]}


class TestSlotSubset:
    """`--slot <slot>=a,b` keeps that subset — hiding one embed of a shared
    slot, which `none` (the whole slot) cannot say."""

    def _pairs(self):
        from stapel_tools.create_project import _frontend_react_entries

        return _frontend_react_entries(["auth", "workspaces", "notifications", "translate"])

    def test_subset_keeps_declared_order(self):
        plan = plan_slots(
            "topbar-sidenav", self._pairs(), auth_gated=True,
            overrides={"topbar.end": "auth.account_menu,notifications.bell"},
        )
        assert [e["id"] for e in plan.slots["topbar.end"]] == [
            "notifications.bell", "auth.account_menu",
        ]

    def test_unknown_member_of_a_subset_is_refused(self):
        with pytest.raises(ValueError, match="'bogus.embed' is not offered for topbar.end"):
            plan_slots(
                "topbar-sidenav", self._pairs(), auth_gated=True,
                overrides={"topbar.end": "notifications.bell,bogus.embed"},
            )

    def test_cli_subset_reaches_the_composition(self, tmp_path):
        import json

        front = _create(
            tmp_path, modules=["core", "auth", "workspaces", "notifications"],
            slots={"topbar.end": "auth.account_menu"},
        ) / "frontend"
        composition = json.loads((front / "stapel.composition.json").read_text())
        assert composition["shell"]["slots"]["topbar.end"] == ["auth.account_menu"]
        assert "NotificationBell" not in (front / "src" / "embeds.generated.tsx").read_text()
