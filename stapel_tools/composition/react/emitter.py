"""The React emitter — the in-project ``frontend/`` (Vite + React + TypeScript).

The first emitter of the composition seam: the same templates and planners
the monolith scaffold has always used, with every write collected into an
:class:`EmitPlan` instead of performed. See ``_frontend_templates``' module
docstring for the delivery canon (§57) and the scripted navigation.

Scripted-fullstack navigation: ``comp.want_auth`` and ``comp.landing`` drive
react-router v7 wiring — routing is active when either is set OR at least one
selected pair carries a ``nav`` mirror. Only THEN does ``src/main.tsx`` mount
the generated ``router`` (``src/routes.tsx``) instead of the bare ``<App/>``;
a selection with none of the three is the bare app, byte for byte.
"""
from __future__ import annotations

import json
from typing import ClassVar

from ..model import Composition, EmitPlan, PlatformRequest

ROOT = "frontend"


class ReactEmitter:
    platform: ClassVar[str] = "react"

    def validate(self, comp: Composition, request: PlatformRequest) -> None:
        from ..._shell_slots import plan_slots
        from ..._shell_templates import DEFAULT_SHELL_TEMPLATE
        from ..model import CompositionError
        from .registry import react_entries

        try:
            plan_slots(
                request.shell or DEFAULT_SHELL_TEMPLATE,
                react_entries(comp.modules),
                auth_gated=comp.want_auth and "auth" in comp.modules,
                overrides=dict(request.slot_overrides) or None,
            )
        except ValueError as exc:
            raise CompositionError(str(exc)) from exc

    def plan(self, comp: Composition, request: PlatformRequest) -> EmitPlan:  # noqa: C901
        from ... import _frontend_templates as F
        from ..._compose_templates import render_tokens
        from ..._shell_slots import plan_page_embeds, plan_slots
        from ..._shell_templates import DEFAULT_SHELL_TEMPLATE
        from ...create_project import (
            _render_frontend_package_json,
            _vite_proxy_rules,
            shell_has_slots,
        )
        from .registry import react_entries as _react_entries

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
        slots = dict(request.slot_overrides) or None
        react_entries = _react_entries(comp.modules)
        # cdn auto-wiring (cdn-scaffold-autowire.md): registers a stopgap `cdn`
        # client in ModulesProvider whenever there is at least one OTHER react
        # pair's client to reuse; the avatarUrlFor helper (src/lib/cdn.ts) is
        # only written when profiles or listings is ALSO selected.
        has_cdn = "cdn" in feature_libs
        # Written for listings too: its runtime takes `listingImageFor` as resolveImage.
        needs_cdn_avatar_lib = has_cdn and bool({"profiles", "listings"} & set(feature_libs))
        want_auth = comp.want_auth
        want_landing = comp.landing
        auth_wired = bool(want_auth) and any(e["key"] == "auth" for e in react_entries)
        any_nav_entries = any(e.get("nav") for e in react_entries)
        routing_active = bool(want_auth) or bool(want_landing) or any_nav_entries

        nav_pairs = F.nav_wired_pairs(react_entries, auth_wired=auth_wired)
        route_plan = (
            F.build_nav_route_plan(nav_pairs)
            if nav_pairs
            else {"absolute_routes": [], "app_children": [], "container_roots": []}
        )
        app_route_present = auth_wired or bool(route_plan["app_children"])
        shell_react_needed = bool(nav_pairs) or app_route_present

        # Composition spec §4: the installed pairs' embeds resolved onto the
        # chosen template. Emitted only where a chrome exists to receive them
        # and the pinned shell takes `slots`; otherwise the container stays.
        slot_plan = plan_slots(
            request.shell or DEFAULT_SHELL_TEMPLATE, react_entries,
            auth_gated=auth_wired, overrides=slots,
        )
        slot_notes = list(slot_plan.notes)
        # Page-slot embeds (`page.actions`) go to the page their scope names,
        # not to the chrome: every mounted entry whose page renders a slot.
        page_plan = plan_page_embeds(
            react_entries, auth_gated=auth_wired,
            pages={
                entry_id: F.page_slots(entry_id, react_entries)
                for entry_id in F.nav_entry_paths(route_plan)
            } if shell_react_needed else {},
        )
        slot_notes.extend(page_plan.notes)
        emit_embeds = (
            routing_active and app_route_present and shell_has_slots()
            and bool(slot_plan.filled)
        )
        embed_slots: dict[str, str] = {}
        embeds_source = None
        if emit_embeds:
            embeds_source, open_notes = F.render_embeds_generated_tsx(
                slot_plan.filled, F.nav_entry_paths(route_plan),
            )
            slot_notes.extend(open_notes)
            embed_slots = {slot: F.slot_component_name(slot) for slot in slot_plan.filled}
        else:
            for slot, embeds in slot_plan.filled.items():
                for embed in embeds:
                    slot_notes.append(
                        f"embed {embed['id']} is not mounted in {slot}: "
                        + ("the pinned @stapel/shell-react takes no slots"
                           if app_route_present else "this project renders no shell")
                    )

        package_json = _render_frontend_package_json(
            r(F.PACKAGE_JSON), react_entries,
            routing_active=routing_active, shell_react_needed=shell_react_needed,
            has_media=has_cdn or "profiles" in feature_libs,
        )
        write("package.json", package_json)
        write("pnpm-workspace.yaml", F.PNPM_WORKSPACE_YAML)
        write(
            "tsconfig.json",
            F.TSCONFIG_JSON_WITH_JSON_MODULE if routing_active else F.TSCONFIG_JSON,
        )
        write("tsconfig.node.json", F.TSCONFIG_NODE_JSON)
        write("vite.config.ts", r(F.VITE_CONFIG_TS))
        write("index.html", r(F.INDEX_HTML))

        mount_files = (
            F.monolith_mount_plan(
                route_plan, pairs=tuple(e["key"] for e in react_entries),
                page_embeds=page_plan.pages,
            )
            if routing_active and shell_react_needed
            else {"pages": {}, "needs_placeholder": False}
        )
        # The container's own copy (section landings, staff gate, landing
        # page, placeholder) reads src/i18n/keys.ts through the pairs' engine.
        container_i18n = bool(react_entries) and bool(
            mount_files["pages"] or mount_files["needs_placeholder"]
            or want_landing or (routing_active and route_plan["container_roots"])
        )
        if react_entries:
            write(
                "src/modules.tsx",
                F.render_modules_tsx(
                    react_entries, has_cdn=has_cdn, shell_react=shell_react_needed,
                    container_i18n=container_i18n,
                    auto_anonymous_actions=comp.auto_anonymous_actions,
                ),
            )

        if needs_cdn_avatar_lib:
            write("src/lib/cdn.ts", F.render_cdn_lib_ts(comp.slug))
        # The mandate source modules.tsx mounts (auth without workspaces): the
        # storefront's, since the derivation is the same one.
        react_keys = {e["key"] for e in react_entries}
        if "auth" in react_keys and "workspaces" not in react_keys:
            write("src/mandateSource.ts", F.render_mandate_source_ts())

        if routing_active:
            write("src/main.tsx", F.render_main_tsx(
                routing_active=True, has_modules=bool(react_entries),
            ))
            if shell_react_needed:
                write(
                    "src/nav.generated.ts",
                    F.render_nav_generated_ts(nav_pairs, auth_wired=auth_wired),
                )
                write("stapel.nav.json", F.STAPEL_NAV_JSON)
                write(
                    "src/__tests__/nav.test.ts",
                    F.NAV_GATE_EMBEDS_TEST_TS if embeds_source else F.NAV_GATE_TEST_TS,
                )
                if embeds_source:
                    write("src/embeds.generated.tsx", embeds_source)
            # One landing page per container-owned section the selection needs
            # (`/app/account`, `/app/admin`). Without them the admin-level
            # screens gdpr and video declare are orphans and vanish silently.
            if auth_wired and "admin.root" in route_plan["container_roots"]:
                write("src/AdminGate.tsx", F.ADMIN_GATE_APP_TSX)
            for root_id in route_plan["container_roots"]:
                component = F.MONOLITH_CONTAINER_ROOTS[root_id]["_local"]
                children = [
                    entry["id"]
                    for pair in nav_pairs
                    for entry in pair["nav"]
                    if entry["placement"].get("parentId") == root_id
                ]
                write(f"src/{component}.tsx", F.render_section_home_tsx(root_id, children))
            pair_keys = tuple(e["key"] for e in react_entries)
            write("src/routes.tsx", F.render_routes_tsx(
                route_plan, auth_wired=auth_wired, want_landing=bool(want_landing),
                app_route_present=app_route_present, has_cdn=has_cdn,
                pairs=pair_keys, embed_slots=embed_slots or None,
                not_found=container_i18n,
            ))
            if container_i18n:
                write("src/NotFoundPage.tsx", F.NOT_FOUND_PAGE_TSX)
            # The mount pass's own files: wrappers for screens that REQUIRE a
            # prop the address carries, and the named placeholder — the same
            # machinery the public storefront emits. One table, two containers.
            mounts = F.monolith_mount_plan(
                route_plan, pairs=pair_keys, page_embeds=page_plan.pages,
            )
            if mounts["pages"] or mounts["needs_placeholder"]:
                write("src/NavPlaceholder.tsx", F.NAV_PLACEHOLDER_TSX)
            if container_i18n:
                write("src/i18n/keys.ts", F.render_storefront_i18n_ts("en", title=comp.title))
            for rel, content in mounts["pages"].items():
                files[f"{ROOT}/{rel}"] = content
            if auth_wired:
                write("src/ProtectedRoute.tsx", F.PROTECTED_ROUTE_TSX)
            if want_landing:
                cta_href = "/login" if auth_wired else "/app"
                write("src/LandingPage.tsx", render_tokens(
                    F.LANDING_PAGE_TSX if container_i18n else F.LANDING_PAGE_STATIC_TSX,
                    {**render_ctx, "CTA_HREF": cta_href},
                ))
        else:
            write("src/main.tsx", F.MAIN_TSX)
            if react_entries:
                write("src/App.tsx", r(F.APP_TSX_WITH_MODULES))
            else:
                write("src/App.tsx", r(F.APP_TSX))

        write("src/vite-env.d.ts", F.VITE_ENV_D_TS)
        write("stapel.theme.json", comp.theme_json)
        # The composition as this scaffold understood it — what Studio's run
        # view reads, and what the generated nav gate checks the slots against.
        mounted = slot_plan.filled if embeds_source else {}
        composition = {
            "shell": {
                "template": slot_plan.template,
                "slots": {slot: [e["id"] for e in embeds] for slot, embeds in mounted.items()},
                "overrides": dict(slot_plan.overrides),
                "dropped": [
                    *slot_plan.dropped,
                    *(e["id"] for embeds in slot_plan.filled.values() for e in embeds
                      if not embeds_source),
                    *page_plan.dropped,
                ],
            },
            "auth": {"model": comp.auth_model, "gated": auth_wired},
            "theme": comp.theme or "default",
            "modules": list(feature_libs),
        }
        if page_plan.pages and routing_active:
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
