"""The React emitter — the in-project ``frontend/`` (Vite + React + TypeScript).

The first emitter of the composition seam: the same templates and planners
the monolith scaffold has always used, with every write collected into an
:class:`EmitPlan` instead of performed. See ``_frontend_templates``' module
docstring for the delivery canon (§57).

Navigation (navigation-templates spec §4.2): the shell ALWAYS mounts. The
resolved ``navigation`` plan (``composition.react.shell``) picks the shipped
chrome — ``<PublicShell/>`` for the storefront header, ``<AppShell/>`` for the
app header — and ONE layout route carries it over every route: the landing,
sign-in, every public screen and the member area alike. The project's own
pages come in through ``stapel.app-nav.json`` + ``src/app.pages.ts``.
"""
from __future__ import annotations

import hashlib
import json
from typing import ClassVar

from ..model import Composition, CompositionError, EmitPlan, PlatformRequest

ROOT = "frontend"

#: The files the regenerate step owns; hashed into `navigation.resolved.generated`.
GENERATED_NAV_FILES: tuple[str, ...] = (
    "src/routes.tsx", "src/nav.generated.ts", "src/chrome.generated.tsx",
    "src/StorefrontShell.tsx", "src/AppChrome.tsx", "src/embeds.generated.tsx",
    "src/AppPage.tsx",
)

#: Physical slots each chrome renders, and the PublicShell prop each feeds.
PUBLIC_SHELL_SLOT_PROPS: dict[str, str] = {
    "topbar.end": "accountSlot", "topbar.center": "searchSlot",
    "browsebar": "categorySlot", "footer": "footer",
}


def nav_hashes(files: dict, prefix: str) -> dict[str, str]:
    """SHA-256 of every generated nav file present in *files*."""
    out = {}
    for rel in GENERATED_NAV_FILES:
        content = files.get(f"{prefix}{rel}")
        if content is not None:
            out[f"{prefix}{rel}"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return out


def slot_plan_for(shell, react_entries: list[dict], request: PlatformRequest, *,
                  auth_gated: bool):
    """The physical slot plan of the resolved chrome, with the embeds the
    semantic slots silenced left out."""
    from ..._shell_slots import plan_slots

    excluded = set(shell.nav.excluded_embeds)
    pairs = [
        {**e, "embeds": [x for x in e.get("embeds") or () if x["id"] not in excluded]}
        for e in react_entries
    ]
    try:
        plan = plan_slots(
            shell.nav.template, pairs, auth_gated=auth_gated,
            overrides=dict(request.slot_overrides) or None,
            renderable=frozenset({shell.nav.template}),
        )
    except ValueError as exc:
        raise CompositionError(str(exc)) from exc
    if shell.shape == "storefront":
        for slot in list(plan.slots):
            if slot not in PUBLIC_SHELL_SLOT_PROPS and plan.slots[slot]:
                for embed in plan.slots[slot]:
                    plan.notes.append(
                        f"embed {embed['id']} wants {slot}, which PublicShell 0.20.1 "
                        "draws no place for")
                    plan.dropped.append(embed["id"])
                plan.slots[slot] = []
    return plan


class ReactEmitter:
    platform: ClassVar[str] = "react"

    def validate(self, comp: Composition, request: PlatformRequest) -> None:
        from .registry import react_entries
        from .shell import plan_react_shell

        entries = react_entries(comp.modules)
        shell = plan_react_shell(comp, entries)
        auth_wired = comp.want_auth and any(e["key"] == "auth" for e in entries)
        slot_plan_for(shell, entries, request, auth_gated=auth_wired)

    def plan(self, comp: Composition, request: PlatformRequest) -> EmitPlan:  # noqa: C901
        from ... import _frontend_templates as F
        from ..._compose_templates import render_tokens
        from ..._shell_slots import plan_page_embeds
        from ...create_project import (
            _render_frontend_package_json,
            _vite_proxy_rules,
        )
        from .registry import react_entries as _react_entries
        from .shell import plan_react_shell

        files: dict[str, str] = {}

        def write(rel: str, content: str) -> None:
            files[f"{ROOT}/{rel}"] = content

        render_ctx = {
            "SLUG": comp.slug,
            "TITLE": comp.title,
            "BACKEND_UPSTREAM_DEFAULT": comp.backend_upstream_default,
            "VITE_PROXY_RULES": _vite_proxy_rules(list(comp.backend_prefixes)),
            "PNPM_VERSION": F.SCAFFOLD_PNPM_VERSION,
        }

        def r(template: str) -> str:
            return render_tokens(template, render_ctx)

        feature_libs = list(comp.modules)
        react_entries = _react_entries(comp.modules)
        # cdn auto-wiring (cdn-scaffold-autowire.md): registers a stopgap `cdn`
        # client in ModulesProvider whenever there is at least one OTHER react
        # pair's client to reuse; the avatarUrlFor helper (src/lib/cdn.ts) is
        # only written when profiles or listings is ALSO selected.
        has_cdn = "cdn" in feature_libs
        # Written for listings too: its runtime takes `listingImageFor` as resolveImage.
        needs_cdn_avatar_lib = has_cdn and bool({"profiles", "listings"} & set(feature_libs))
        want_landing = comp.landing
        auth_wired = bool(comp.want_auth) and any(e["key"] == "auth" for e in react_entries)

        shell = plan_react_shell(comp, react_entries)
        nav = shell.nav
        storefront = shell.routes == "public"
        route_plan = shell.route_plan
        pair_keys = tuple(e["key"] for e in react_entries)

        slot_plan = slot_plan_for(shell, react_entries, request, auth_gated=auth_wired)
        slot_notes = list(nav.notes) + list(slot_plan.notes)
        # Page-slot embeds (`page.actions`) go to the page their scope names.
        page_plan = plan_page_embeds(
            [{**e, "embeds": [x for x in e.get("embeds") or ()
                              if x["id"] not in set(nav.excluded_embeds)]}
             for e in react_entries],
            auth_gated=auth_wired,
            pages={entry_id: F.page_slots(entry_id, react_entries) for entry_id in shell.paths},
        )
        slot_notes.extend(page_plan.notes)
        filled = slot_plan.filled
        embeds_source = None
        if filled:
            embeds_source, open_notes = F.render_embeds_generated_tsx(
                filled, shell.paths, guest_variants=True,
            )
            slot_notes.extend(open_notes)

        package_json = _render_frontend_package_json(
            r(F.PACKAGE_JSON), react_entries,
            routing_active=True, shell_react_needed=True,
            has_media=has_cdn or "profiles" in feature_libs,
        )
        write("package.json", package_json)
        write("pnpm-workspace.yaml", F.PNPM_WORKSPACE_YAML)
        write("tsconfig.json", F.TSCONFIG_JSON_WITH_JSON_MODULE)
        write("tsconfig.node.json", F.TSCONFIG_NODE_JSON)
        write("vite.config.ts", r(F.VITE_CONFIG_TS))
        write("index.html", r(F.INDEX_HTML))

        if storefront:
            mounts = F.public_mount_plan(route_plan, {}, pairs=pair_keys,
                                         page_embeds=page_plan.pages)
        else:
            mounts = F.monolith_mount_plan(route_plan, pairs=pair_keys,
                                           page_embeds=page_plan.pages)
        write("src/modules.tsx", F.render_modules_tsx(
            react_entries, has_cdn=has_cdn, shell_react=True, container_i18n=True,
            auto_anonymous_actions=comp.auto_anonymous_actions,
        ))
        if needs_cdn_avatar_lib:
            write("src/lib/cdn.ts", F.render_cdn_lib_ts(comp.slug))
        # The mandate source modules.tsx mounts (auth without workspaces).
        react_keys = {e["key"] for e in react_entries}
        if "auth" in react_keys and "workspaces" not in react_keys:
            write("src/mandateSource.ts", F.render_mandate_source_ts())
        write("src/main.tsx", F.render_main_tsx(routing_active=True, has_modules=True))

        write("src/nav.generated.ts", F.render_shell_nav_generated_ts(
            shell.manifests, paths=shell.paths, has_accounts=shell.has_accounts,
            guest_wall=shell.guest_wall,
        ))
        write("stapel.nav.json", F.render_shell_nav_overrides_json(
            shell.overrides, seed_account=storefront and shell.has_accounts))
        write("src/chrome.generated.tsx", F.render_shell_chrome_tsx(
            shape=shell.shape, name="AppChrome", title=comp.title, filled=filled,
            sign_in_path=shell.sign_in_path, has_accounts=shell.has_accounts,
            auth_wired=auth_wired, badge_ids=list(shell.paths),
            shell_providers=F._shell_providers(pair_keys) if shell.routes == "app" else [],
            phone_chrome="dock" if nav.primary["compact"] == "island" else "drawer",
            legal_links=shell.legal_links,
        ))
        if embeds_source:
            write("src/embeds.generated.tsx", embeds_source)

        home = ("Home", "./pages/Home.js")
        if storefront:
            if want_landing:
                home = ("LandingPage", "./LandingPage.js")
            else:
                home = ("StorefrontHome", "./StorefrontHome.js")
                write("src/StorefrontHome.tsx", F.STOREFRONT_HOME_TSX)
            write("src/routes.tsx", F.render_public_routes_tsx(
                route_plan, {}, pairs=pair_keys,
                chrome=("AppChrome", "./chrome.generated.js"), home=home,
                gated=shell.has_accounts, not_found=True, page_embeds=page_plan.pages,
            ))
            if shell.has_accounts:
                write("src/MemberGate.tsx", F.MEMBER_GATE_TSX)
                write("src/AccountHome.tsx", F.ACCOUNT_HOME_TSX)
            if any(e["id"] == "admin.root" for m in shell.manifests for e in m["entries"]):
                write("src/AdminHome.tsx", F.ADMIN_HOME_TSX)
                if "auth" in pair_keys:
                    write("src/AdminGate.tsx", F.ADMIN_GATE_TSX)
        else:
            app_route_present = auth_wired or bool(route_plan["app_children"])
            if not want_landing and not app_route_present:
                write("src/pages/Home.tsx", r(F.HOME_TSX))
            if auth_wired and "admin.root" in route_plan["container_roots"]:
                write("src/AdminGate.tsx", F.ADMIN_GATE_APP_TSX)
            for root_id in route_plan["container_roots"]:
                component = F.MONOLITH_CONTAINER_ROOTS[root_id]["_local"]
                children = [
                    entry["id"]
                    for pair in shell.nav_pairs
                    for entry in pair["nav"]
                    if entry["placement"].get("parentId") == root_id
                ] + [e["id"] for e in shell.app_entries
                     if e["placement"].get("parentId") == root_id]
                write(f"src/{component}.tsx", F.render_section_home_tsx(root_id, children))
            write("src/routes.tsx", F.render_routes_tsx(
                route_plan, auth_wired=auth_wired, want_landing=bool(want_landing),
                app_route_present=app_route_present, has_cdn=has_cdn, pairs=pair_keys,
                not_found=True, home=home,
            ))
            if auth_wired:
                write("src/ProtectedRoute.tsx", F.PROTECTED_ROUTE_TSX)
            if app_route_present:
                shell.reach.setdefault("/app", "section")
                nav.resolved["reach"] = dict(sorted(shell.reach.items()))
        write("src/NotFoundPage.tsx", F.NOT_FOUND_PAGE_TSX)
        if mounts["pages"] or mounts["needs_placeholder"] or shell.app_entries:
            write("src/NavPlaceholder.tsx", F.NAV_PLACEHOLDER_TSX)
        write("src/i18n/keys.ts", F.render_storefront_i18n_ts("en", title=comp.title))
        for rel, content in mounts["pages"].items():
            files[f"{ROOT}/{rel}"] = content
        if want_landing:
            write("src/LandingPage.tsx", render_tokens(
                F.LANDING_PAGE_TSX, {**render_ctx, "CTA_HREF": shell.sign_in_path or "/"},
            ))
        # The own-pages seam: the binding file is the project's (written once),
        # the mount is generated.
        write("src/app.pages.ts", F.APP_PAGES_TS)
        if shell.app_entries:
            write("src/AppPage.tsx", F.APP_PAGE_TSX)

        write("src/__tests__/nav.test.ts", F.render_shell_nav_test_ts())
        write("src/__tests__/shell.test.tsx", F.render_shell_test_tsx(
            has_modules=True, sign_in_path=shell.sign_in_path,
            shell_testid="public-shell" if shell.shape == "storefront" else "app-shell",
        ))
        write("src/__tests__/shell-lint.test.ts", F.SHELL_LINT_TEST_TS)
        write("src/__tests__/generated.test.ts", F.render_generated_hash_test_ts(
            [], frontend_prefix=f"{ROOT}/"))

        write("src/vite-env.d.ts", F.VITE_ENV_D_TS)
        write("stapel.theme.json", comp.theme_json)
        nav.resolved["generated"] = nav_hashes(files, f"{ROOT}/")
        # The composition as this scaffold understood it — what Studio's run
        # view reads, and what the generated gates check the app against.
        composition = {
            "shell": {
                "template": slot_plan.template,
                "slots": {slot: [e["id"] for e in embeds] for slot, embeds in filled.items()},
                "overrides": dict(slot_plan.overrides),
                "dropped": [*slot_plan.dropped, *page_plan.dropped],
            },
            "navigation": {"block": comp.navigation, "resolved": nav.resolved},
            "auth": {"model": comp.auth_model, "gated": auth_wired},
            "theme": comp.theme or "default",
            "modules": list(feature_libs),
        }
        if page_plan.pages:
            composition["pages"] = {
                entry_id: {slot: [e["id"] for e in embeds] for slot, embeds in slots_.items()}
                for entry_id, slots_ in page_plan.pages.items()
            }
        write("stapel.composition.json", json.dumps(composition, indent=2) + "\n")
        write("eslint.config.js", F.ESLINT_CONFIG_JS)
        write(".gitignore", F.GITIGNORE)
        write("Dockerfile", F.DOCKERFILE)
        # `COPY . .` must not carry a host node_modules (other platform's
        # binaries) over the one the image just installed.
        write(".dockerignore", F.DOCKERIGNORE)
        # The publish step the Dockerfile's export stage runs: a build-id swap
        # + prune instead of `rm -rf /output/*` (FRONTEND_PUBLISH_SH).
        write("frontend-publish.sh", F.FRONTEND_PUBLISH_SH)
        write("README.md", r(F.README_MD))

        return EmitPlan(
            platform=self.platform,
            root=ROOT,
            files=files,
            composition=composition,
            notes=tuple(slot_notes),
        )
