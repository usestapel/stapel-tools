"""``stapel-frontend-repo-init`` — give a SEPARATE frontend repository the
publishing half of the delivery canon.

Why a separate command exists at all
------------------------------------
``stapel-create-project --type monolith`` scaffolds the frontend *inside* the
project, so compose can build it and the §57 canon is complete in one repo.
A microservice project's frontend lives in its OWN repository — that is what
makes it a microservice project — and nothing in this toolchain ever wrote
into that repo. So the delivery canon only ever existed on the CONSUMING side:
the backend declared a volume and a one-shot service, and the frontend repo had
no idea it was supposed to publish anything into them.

That gap is not hypothetical. Measured on ironmemo (2026-08-05):
``ironmemo-frontend`` has no ``Dockerfile`` and no ``.gitlab-ci.yml`` at all —
only a locally built ``dist/``. The backend's nginx served ``root
/frontend-react``, a bind onto a host directory that both
``scripts/deploy_stand.sh`` and the backend's CI explicitly EXCLUDED from
rsync. Nobody could have delivered a build even if they had wanted to. For
months this read as "the frontend does not update" and was diagnosed as
caching.

What it writes
--------------
``Dockerfile``            multi-stage; the ``export`` stage is a dist CARRIER,
                          not an nginx image (the project's own nginx stays the
                          single boundary owning reserved paths, TLS, the proxy
                          table and the cache canon).
``frontend-publish.sh``   the publish step: new build into its own directory,
                          ``current`` repointed, N previous kept so tabs open
                          across a deploy can still fetch their chunks.
``.gitlab-ci.yml`` job    build + push ``sha-<gitsha>``, an IMMUTABLE tag.
   or ``.github/...``     Never ``latest``: with a moving tag "which frontend
                          is on this stand" has no answer and a redeploy
                          silently changes the app. ``stapel-frontend-delivery-
                          lint`` FED002 refuses a mutable tag on the consuming
                          side too.

What it deliberately does NOT do
--------------------------------
It does not bump the tag in the backend repo. The pin lives there, in the env
template, in git — that is what makes the backend↔frontend pair readable from
one repository's history. Automating the bump is a CI-to-CI trigger the two
repos have to agree on; this command only makes the artifact exist.

``--surface public`` — the SOURCE half too
------------------------------------------
The gap above was one of two. The other: a split-repo frontend had a delivery
pipeline and NO SOURCE, so somebody wrote the container by hand every time —
the providers, the route tree, the access gate, the nav resolution — and every
hand-written copy of those drifted from the pairs it mounted.

``--surface public --pairs <csv>`` writes the container itself: one runtime,
one provider and one catalogue per pair; the mandate seam; the nav trees
resolved for BOTH audiences by name; the route tree; the member gate; the
theme and nav override files; ``reserved-paths.json``; and a page that NAMES
the absence wherever a declared screen needs something only a container can
supply. It is not a second generator: the renderers live in
``_frontend_templates`` beside the monolith's, because a storefront and an
in-project frontend disagree about chrome and about who may see which screen,
and about nothing else.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import _frontend_templates as F
from ._frontend_templates import (
    DOCKERIGNORE,
    FRONTEND_PUBLISH_SH,
    detect_package_manager,
    render_dockerfile,
)

# GitLab. Mirrors what the backend repos in this fleet already use.
GITLAB_CI_JOB = """\
# ─── Frontend delivery (stapel canon) ───────────────────────────────────────
# Publishes a dist-CARRIER image the backend project's compose pulls by an
# IMMUTABLE tag. See the backend's docker-compose.yml `frontend-build` service
# and scripts/env.stand.template's FRONTEND_TAG.
#
# sha-$CI_COMMIT_SHORT_SHA, never `latest`: a moving tag makes "which frontend
# is on this stand" unanswerable and lets a redeploy silently change the app.
# The backend repo holds the pin, so bumping the frontend is a commit THERE —
# that is what keeps the backend↔frontend pair readable from one history.
publish_frontend:
  stage: build
  image: docker:27
  services:
    - docker:27-dind
  variables:
    IMAGE: "$CI_REGISTRY_IMAGE"
    TAG: "sha-$CI_COMMIT_SHORT_SHA"
  rules:
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
  script:
    - docker login -u "$CI_REGISTRY_USER" -p "$CI_REGISTRY_PASSWORD" "$CI_REGISTRY"
    - docker build --target export -t "$IMAGE:$TAG" .
    - docker push "$IMAGE:$TAG"
    - echo "Pin this in the backend repo — scripts/env.stand.template:"
    - echo "  FRONTEND_IMAGE=$IMAGE"
    - echo "  FRONTEND_TAG=$TAG"
"""

GITHUB_WORKFLOW = """\
# Frontend delivery (stapel canon) — publishes a dist-CARRIER image the backend
# project's compose pulls by an IMMUTABLE tag. sha-<gitsha>, never `latest`:
# a moving tag makes "which frontend is on this stand" unanswerable.
name: Publish frontend image

on:
  push:
    branches: [main]

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Build and push
        run: |
          IMAGE=ghcr.io/${{ github.repository }}
          TAG=sha-$(echo "${{ github.sha }}" | cut -c1-8)
          docker build --target export -t "$IMAGE:$TAG" .
          docker push "$IMAGE:$TAG"
          echo "Pin in the backend repo — scripts/env.stand.template:"
          echo "  FRONTEND_IMAGE=$IMAGE"
          echo "  FRONTEND_TAG=$TAG"
"""

README_SECTION = """\

## Delivery to a stand (stapel canon)

This app is delivered to a stand as a **dist-carrier image**, not as files
copied by the backend's deploy script.

* CI builds `--target export` and pushes `sha-<gitsha>` — an immutable tag.
* The BACKEND repo pins that tag in `scripts/env.stand.template`. Bumping the
  frontend on a stand is a commit in the backend repo; that is what makes
  "which frontend goes with this backend" answerable from one history.
* On deploy, the backend's one-shot `frontend-build` service runs this image,
  which publishes `dist/` into the volume its nginx serves — a new directory
  per build with `current` repointed, keeping the previous builds so a browser
  tab open across the deploy can still fetch its content-hashed chunks.

Do not edit `FRONTEND_TAG` in the stand's `.env`: deploy regenerates `.env`
from the template on every run, so the edit disappears without a word.
"""


# The fleet-wide prefixes every storefront proxies whether or not it installs
# the matching pair: sign-in is generated unconditionally (`auth`), and a
# fleet's own list comes from its modules' `urls_v1.py`. `--prefixes` adds the
# rest (gdpr, geo, currencies, moderation, billing, a composite's own root…).
# Nothing is invented here: an absent prefix is simply not proxied in DEV, and
# in production the fleet's nginx is the boundary either way.
ALWAYS_PROXIED = ("auth",)


class SurfaceError(SystemExit):
    """A refusal with a sentence, not a traceback."""


def public_source_plan(
    *,
    name: str,
    title: str,
    pairs: list[str],
    locale: str,
    realtime: bool,
    extra_prefixes: list[str],
    doc_type: str = "",
) -> dict:
    """Every file the SOURCE half of a public container consists of, as
    ``{relative path: content}`` — pure, so the whole emission is testable
    without touching a filesystem.

    The pair list is resolved through ``create_project.FRONTEND_REACT_LIBS``:
    one registry for the package name, the pinned minor, the provider/runtime/
    catalogue symbols AND the nav mirror, so a container cannot install a pair
    at one version and mount its nav at another.
    """
    from .create_project import (
        FRONTEND_REACT_ANTD_DEPS,
        FRONTEND_REACT_CORE_DEPS,
        FRONTEND_REACT_LIBS,
        FRONTEND_ROUTER_DEPS,
        FRONTEND_SHELL_REACT_VERSION,
    )
    from .reserved_paths import reserved_prefixes_for

    unknown = [key for key in pairs if key not in FRONTEND_REACT_LIBS]
    if unknown:
        known = ", ".join(sorted(FRONTEND_REACT_LIBS))
        raise SurfaceError(
            f"frontend-repo-init: no registered pair for {unknown!r}. "
            f"Registered pairs: {known}. A pair is registered once, in "
            "create_project.FRONTEND_REACT_LIBS, with its package, its pinned "
            "minor and its nav mirror together."
        )
    if locale != "en" and locale not in F.PAIR_LOCALES:
        raise SurfaceError(
            f"frontend-repo-init: the pairs publish catalogues for "
            f"{', '.join(F.PAIR_LOCALES)} (plus their inlined `en` floor), not "
            f"{locale!r}. Generating for it would register English copy under a "
            "locale nobody translated and call it done."
        )

    entries = [{"key": key, **FRONTEND_REACT_LIBS[key]} for key in pairs]
    nav_pairs = F.public_nav_pairs(entries)
    manifests = F.public_nav_manifests(nav_pairs, app_package=name)
    plan = F.build_public_route_plan(manifests)
    options = {"doc_type": doc_type}
    # The selection itself is an input to mounting: a CROSS-PAIR screen (the
    # listing composer) is wired only when every pair it composes is installed.
    selected = tuple(pairs)
    mounts = F.public_mount_plan(plan, options, pairs=selected)

    modules = []
    for key in (*ALWAYS_PROXIED, *pairs, *extra_prefixes):
        if key not in modules:
            modules.append(key)
    reserved = reserved_prefixes_for(modules)
    # The Vite table proxies the same surfaces, plus the two framework-wide
    # static roots the reserved list carries for the lint rule.
    proxy_prefixes = [p for p in reserved]

    files: dict[str, str] = {
        "package.json": F.render_public_package_json(
            entries,
            name=name,
            core_version=FRONTEND_REACT_CORE_DEPS["@stapel/core"],
            query_version=FRONTEND_REACT_CORE_DEPS["@tanstack/react-query"],
            antd_version=FRONTEND_REACT_ANTD_DEPS["antd"],
            tokens_antd_version=FRONTEND_REACT_ANTD_DEPS["@stapel/tokens-antd"],
            shell_version=FRONTEND_SHELL_REACT_VERSION,
            router_version=FRONTEND_ROUTER_DEPS["react-router"],
        ),
        "tsconfig.json": F.TSCONFIG_JSON_WITH_JSON_MODULE,
        "tsconfig.node.json": F.TSCONFIG_NODE_JSON,
        "vite.config.ts": F.render_public_vite_config_ts(proxy_prefixes),
        "index.html": F.render_public_index_html(title),
        "eslint.config.js": F.render_public_eslint_config_js(),
        ".gitignore": F.GITIGNORE,
        "reserved-paths.json": json.dumps({"reservedPathPrefixes": reserved}, indent=2) + "\n",
        "stapel.theme.json": F.THEME_JSON.replace("{{TITLE}}", title),
        "stapel.nav.json": F.render_public_nav_overrides_json(),
        "src/main.tsx": F.PUBLIC_MAIN_TSX,
        "src/vite-env.d.ts": F.VITE_ENV_D_TS,
        "src/modules.tsx": F.render_public_modules_tsx(
            entries, locale=locale, realtime=realtime
        ),
        "src/mandateSource.ts": F.render_mandate_source_ts(),
        "src/nav.generated.ts": F.render_public_nav_generated_ts(manifests),
        "src/routes.tsx": F.render_public_routes_tsx(plan, options, pairs=selected),
        "src/MemberGate.tsx": F.MEMBER_GATE_TSX,
        "src/StorefrontShell.tsx": F.render_storefront_shell_tsx(
            [e["id"] for m in manifests for e in m["entries"]]
        ),
        "src/StorefrontHome.tsx": F.STOREFRONT_HOME_TSX,
        "src/AccountHome.tsx": F.ACCOUNT_HOME_TSX,
        "src/i18n/keys.ts": F.render_storefront_i18n_ts(locale),
        "README.md": render_public_readme(
            name=name, title=title, pairs=pairs, locale=locale, realtime=realtime
        ),
    }
    # `/admin` exists only when a selected pair hangs a screen from it (see
    # ADMIN_ROOT_ENTRY): the container declares the root so those screens
    # resolve, and writes its landing page in the same breath.
    if any(
        m["entries"] and any(e["id"] == "admin.root" for e in m["entries"])
        for m in manifests
    ):
        files["src/AdminHome.tsx"] = F.ADMIN_HOME_TSX
        # The staff gate needs a staff fact; auth-react's session is where it
        # lives. Without that pair the admin routes stay member-gated and each
        # pane refuses on its own — no gate that only pretends to check.
        if "auth" in selected:
            files["src/AdminGate.tsx"] = F.ADMIN_GATE_TSX
    if mounts["needs_placeholder"]:
        files["src/NavPlaceholder.tsx"] = F.NAV_PLACEHOLDER_TSX
    files.update(mounts["pages"])
    # Route wrappers import RouteParamProblem from NavPlaceholder.tsx, so the
    # file has to exist whenever a wrapper does — even if nothing needed the
    # placeholder itself.
    if mounts["pages"] and "src/NavPlaceholder.tsx" not in files:
        files["src/NavPlaceholder.tsx"] = F.NAV_PLACEHOLDER_TSX
    return {"files": files, "plan": plan, "mounts": mounts, "entries": entries}


def render_public_readme(
    *, name: str, title: str, pairs: list[str], locale: str, realtime: bool
) -> str:
    """The container's README — including the honest state of the realtime
    seam, which is the whole point of the `--realtime` flag being a documented
    switch rather than a silent one."""
    pair_lines = "\n".join(f"* `@stapel/{key}-react`" for key in pairs)
    if realtime:
        realtime_section = (
            "Generated with `--realtime`. What that actually turned on today is "
            "ONE thing, and it is named rather than implied: `@stapel/chat-react`'s "
            "own socket transport is left to derive its endpoint from the API base "
            "URL (`/ws/chat/` at the same origin), instead of being switched off "
            "with `realtime: { socketUrl: null }`. The pair carries both transports "
            "behind one seam and degrades to polling on its own when the handshake "
            "fails.\n\n"
            "**There is no `@stapel/realtime` package.** `stapel-realtime` is a "
            "Python library (the Channels/Redis delivery substrate); its browser "
            "half — the `useSignalInvalidate` seam every pair would share — is not "
            "built and not published. So `--realtime` cannot wire a general "
            "realtime primitive, and this paragraph exists so nobody reads the flag "
            "as if it had. When that package ships, the migration is one file per "
            "pair, and chat's is already isolated to `src/flows/freshness.ts` "
            "inside the pair.\n\n"
            "Requirement: the fleet must serve ASGI and mount "
            "`stapel_chat.routing.websocket_urlpatterns`. On a WSGI deployment "
            "re-generate without the flag — every tab will otherwise fail a "
            "handshake several times before falling back."
        )
    else:
        realtime_section = (
            "Sockets are OFF, stated rather than discovered: `src/modules.tsx` "
            "passes `realtime: { socketUrl: null }` to the chat runtime, which is "
            "what a WSGI deployment needs. Without it the pair would derive a "
            "socket base and every tab would fail a handshake several times "
            "before falling back to polling.\n\n"
            "Re-generate with `--realtime` on an ASGI fleet that mounts "
            "`stapel_chat.routing.websocket_urlpatterns`. Note there is no "
            "`@stapel/realtime` package yet — the flag turns on the chat pair's "
            "OWN socket transport and nothing more."
        )
    return f"""# {title}

The public storefront container, generated by `stapel-frontend-repo-init
--surface public`. It is a SEPARATE repository from the fleet's backends on
purpose: it ships to a stand as a dist-carrier image, and the fleet's own
nginx stays the single boundary that owns reserved paths, TLS, the proxy table
and the cache canon.

## Installed pairs

{pair_lines}

Each contributes its runtime, its provider and its i18n catalogue in
`src/modules.tsx`, and — where it declares one — its nav manifest in
`src/nav.generated.ts`. A pair with no nav manifest renders inside another
pair's screen and has no route of its own; that is a decision recorded in the
pair, not an omission here.

Locale: `{locale}`. Every pair's catalogue for it is registered, then this
container's own English copy last (the container's copy is product voice — a
generator does not write a product's sentences in a language it was not given).

## Realtime

{realtime_section}

## What generation did NOT do, and where to look

Generation stops at the closed list of things it can know. Everything else is
the wiring step, and it is a LIST, not an open field:

1. the cross-pair slots — a card renderer for search results, a category's
   feature schema for the facet labels and for the composer;
2. the chrome slots in `src/StorefrontShell.tsx` — brand, header search,
   category strip, account menu, footer;
3. the two composite pages, `/` and `/account` — each is currently a page that
   says what belongs there;
4. theme values in `stapel.theme.json`, then `npm run gen:tokens`;
5. cross-pair navigation as `<Link>`s, never `window.location`;
6. the action gates for an anonymous visitor — a named reason and a sign-in
   CTA carrying `?next=`, never a hidden button.

Wherever generation refused to mount a declared screen, the page it emitted
NAMES the entry, the component and the exact props you have to supply. Search
the source for `NavPlaceholder` to find them all.

## Checks

```
npm install
npm run lint     # 0 errors — the guardrails read reserved-paths.json and the key list
npm run build    # tsc --noEmit && vite build
```
"""


def _write(path: Path, content: str, *, force: bool, executable: bool = False) -> str:
    if path.exists() and not force:
        return f"  skipped (exists): {path.name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if executable:
        path.chmod(0o755)
    return f"  wrote: {path.name}"


def write_public_surface(
    repo: Path,
    *,
    name: str,
    title: str,
    pairs: list[str],
    locale: str,
    realtime: bool,
    extra_prefixes: list[str],
    doc_type: str = "",
    force: bool = False,
) -> list[str]:
    """Write the SOURCE half of a public container into *repo*, creating it if
    it does not exist yet (unlike the delivery half, this one is what MAKES
    the repository a frontend repository, so refusing an empty directory would
    refuse the only case it is for)."""
    result = public_source_plan(
        name=name,
        title=title,
        pairs=pairs,
        locale=locale,
        realtime=realtime,
        extra_prefixes=extra_prefixes,
        doc_type=doc_type,
    )
    repo.mkdir(parents=True, exist_ok=True)
    out = [f"  surface: public ({len(result['files'])} files, pairs: {', '.join(pairs)})"]
    for rel, content in sorted(result["files"].items()):
        out.append(_write(repo / rel, content, force=force))
    placeholders = [
        entry_id
        for entry_id, element in result["mounts"]["elements"].items()
        if element.startswith("<NavPlaceholder")
    ]
    if placeholders:
        out.append(
            "  NAMED GAPS (a page was emitted that says what is missing, not a "
            "broken mount): " + ", ".join(sorted(placeholders))
        )
    return out


def init_frontend_repo(repo: Path, *, ci: str = "gitlab", force: bool = False) -> list[str]:
    """Write the publishing half of the delivery canon into *repo*."""
    if not repo.is_dir():
        raise SystemExit(f"frontend-repo-init: not a directory: {repo}")
    if not (repo / "package.json").is_file():
        raise SystemExit(
            f"frontend-repo-init: {repo} has no package.json — this command "
            "writes a build+publish pipeline for a JS frontend repository, and "
            "refuses to scatter one into a directory that is not one."
        )

    # The LOCKFILE decides the install step. `npm ci` in a pnpm repo does not
    # fail loudly — it resolves a DIFFERENT dependency tree than every
    # developer has, and the image you ship stops matching the app anyone
    # tested. ironmemo-frontend is pnpm; the scaffold's own frontend is npm.
    pm = detect_package_manager(repo)
    out = [
        f"  package manager: {pm} (from the lockfile)",
        _write(repo / "Dockerfile", render_dockerfile(pm), force=force),
        _write(repo / ".dockerignore", DOCKERIGNORE, force=force),
        _write(
            repo / "frontend-publish.sh",
            FRONTEND_PUBLISH_SH,
            force=force,
            executable=True,
        ),
    ]

    if ci == "gitlab":
        target = repo / ".gitlab-ci.yml"
        if target.exists() and not force:
            out.append(
                "  .gitlab-ci.yml exists — NOT merged automatically. Append the "
                "publish_frontend job below by hand (a wrong merge into an "
                "existing pipeline is worse than no merge):\n"
                + "\n".join("    " + ln for ln in GITLAB_CI_JOB.splitlines())
            )
        else:
            out.append(_write(target, GITLAB_CI_JOB, force=force))
    elif ci == "github":
        out.append(
            _write(
                repo / ".github" / "workflows" / "publish-frontend.yml",
                GITHUB_WORKFLOW,
                force=force,
            )
        )
    elif ci != "none":
        raise SystemExit(f"frontend-repo-init: unknown --ci {ci!r}")

    readme = repo / "README.md"
    if readme.is_file():
        text = readme.read_text()
        if "Delivery to a stand (stapel canon)" not in text:
            readme.write_text(text.rstrip("\n") + "\n" + README_SECTION)
            out.append("  appended: README.md delivery section")
    return out


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        prog="stapel-frontend-repo-init",
        description=(
            "Write a SEPARATE frontend repository for a split-repo microservice "
            "project: always the publishing half of the delivery canon "
            "(Dockerfile + publish script + CI job), and with `--surface "
            "public` the container's React source half as well."
        ),
    )
    p.add_argument("repo", type=Path, help="path to the frontend repository")
    p.add_argument(
        "--ci",
        default="gitlab",
        choices=("gitlab", "github", "none"),
        help="which CI to write the publish job for (default: gitlab)",
    )
    p.add_argument(
        "--surface",
        default="delivery",
        choices=("delivery", "public"),
        help=(
            "delivery (default): the publishing half only, into a repo that "
            "already has a package.json. public: ALSO generate the container's "
            "source — providers, routes, nav, the member gate — for an "
            "anonymous public surface."
        ),
    )
    p.add_argument(
        "--pairs",
        default="",
        help=(
            "comma-separated pair keys to install (--surface public), e.g. "
            "'search,listings,categories'. Each must be registered in "
            "create_project.FRONTEND_REACT_LIBS."
        ),
    )
    p.add_argument(
        "--preset",
        default="",
        help=(
            "a composite product shape instead of a hand-listed --pairs: "
            "shop, classified, booking, social "
            "(create_project.FRONTEND_COMPOSITES). A composite backend mounts "
            "no URLs of its own — it is a named set of modules — and its "
            "frontend counterpart is the same thing one layer up: the member "
            "pairs, the container-owned nav parents that make their entries "
            "resolve, and the container's own pages. Members the fleet has no "
            "react pair for yet are PRINTED by name, never silently dropped."
        ),
    )
    p.add_argument(
        "--name",
        default=None,
        help="npm package name for the container (default: the repo dir name)",
    )
    p.add_argument("--title", default=None, help="display title (default: --name)")
    p.add_argument(
        "--locale",
        default="en",
        help=(
            "the app's locale. `en` is every pair's inlined floor; "
            f"{'/'.join(F.PAIR_LOCALES)} additionally register that pair's own "
            "published catalogue."
        ),
    )
    p.add_argument(
        "--realtime",
        action="store_true",
        help=(
            "leave the chat pair's socket transport enabled (an ASGI fleet "
            "mounting stapel_chat's websocket routes). There is no "
            "@stapel/realtime package yet, so this flag turns on the chat "
            "pair's OWN socket half and says so in the generated README — it "
            "does not silently pretend to wire a fleet-wide primitive."
        ),
    )
    p.add_argument(
        "--prefixes",
        default="",
        help=(
            "extra fleet module prefixes to proxy in dev and reserve for the "
            "route lint, comma-separated (e.g. 'gdpr,geo,currencies'). The "
            "installed pairs' own prefixes and auth are always included."
        ),
    )
    p.add_argument(
        "--doc-type",
        default="",
        help=(
            "the search doc type this fleet indexes (its "
            "STAPEL_SEARCH['SOURCES'] key, e.g. 'listing'). Required for the "
            "search results page to be MOUNTED: which types exist is a "
            "deployment fact, and a guessed one sends every search to a type "
            "the backend refuses from a page that looks wired. Omitted, the "
            "page is emitted as a named gap instead."
        ),
    )
    p.add_argument(
        "--force", action="store_true", help="overwrite files that already exist"
    )
    args = p.parse_args(argv)

    repo = args.repo.resolve()
    if args.surface == "public":
        from .create_project import CompositeError, composite_pairs, composite_report

        pairs = [k.strip() for k in args.pairs.split(",") if k.strip()]
        if args.preset:
            try:
                preset_pairs = composite_pairs(args.preset)
            except CompositeError as exc:
                raise SurfaceError(f"frontend-repo-init: {exc}") from exc
            # --pairs beside a preset ADDS, never replaces: a fleet that runs
            # one more module than its shape says is normal, and silently
            # dropping either list would be the surprising behaviour.
            pairs = preset_pairs + [k for k in pairs if k not in preset_pairs]
            for line in composite_report(args.preset):
                print("  " + line, file=sys.stderr)
        if not pairs:
            raise SurfaceError(
                "frontend-repo-init: --surface public needs --pairs or "
                "--preset. A container with no pairs is an empty page with a "
                "menu, and generating one would only look like progress."
            )
        name = args.name or repo.name
        title = args.title or name
        extra = [k.strip() for k in args.prefixes.split(",") if k.strip()]
        for line in write_public_surface(
            repo,
            name=name,
            title=title,
            pairs=pairs,
            locale=args.locale,
            realtime=args.realtime,
            extra_prefixes=extra,
            doc_type=args.doc_type,
            force=args.force,
        ):
            print(line, file=sys.stderr)
    elif args.pairs or args.preset or args.realtime or args.name or args.title or args.doc_type:
        raise SurfaceError(
            "frontend-repo-init: --pairs/--preset/--realtime/--name/--title/--doc-type only mean "
            "something with --surface public. Passing them to the delivery "
            "half would do nothing, and doing nothing quietly is how a flag "
            "becomes folklore."
        )

    for line in init_frontend_repo(repo, ci=args.ci, force=args.force):
        print(line, file=sys.stderr)
    print(
        "\nNext: pin the pushed tag in the BACKEND repo's "
        "scripts/env.stand.template (FRONTEND_IMAGE / FRONTEND_TAG). The pin "
        "lives in git, never in the stand's .env — deploy regenerates .env "
        "from the template every run.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
