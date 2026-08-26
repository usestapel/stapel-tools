# Changelog

## [Unreleased]

## [0.55.4] — 2026-08-26

### The scaffold hands a pair a test task the CI runner can actually finish

Five pairs came off `stapel-new-react-lib` this week and every one of them
inherited the same two defects, both in what the scaffold writes about testing.

The first: the generated `package.json` ran `test/prodBundlePurity.test.ts`
inside the default `vitest run`. That test shells out to `npm pack --dry-run`
to prove no demo or showcase code reaches the tarball — 7 to 50 seconds cold,
and on the CI runner's parallel turbo graph that is enough to time the whole
package out. The proof is worth having; paying for it on every `pnpm test` is
not. The fleet convention (`packages/tasks-react`) splits it, and the scaffold
now writes that split:

```json
"test": "tsc -p tsconfig.demo.json && vitest run --exclude test/prodBundlePurity.test.ts",
"test:pack": "vitest run test/prodBundlePurity.test.ts"
```

The second: the generated `vitest.config.ts` carried vitest's 5s default budget
and the pair shipped no setup file at all. Both bite the moment a demo mounts an
antd surface. vitest's `testTimeout` bounds the test, but testing-library polls
its own 1s `asyncUtilTimeout` inside it and throws first — against work that was
still in flight — and the first mount in a file pays antd's one-time CSS-in-JS
generation before a single assertion runs. jsdom then supplies neither
`matchMedia` (antd's responsive observer asks on mount) nor `ResizeObserver`
(`Select`'s dropdown alignment), and throws "Not implemented" on the
pseudo-element `getComputedStyle` form that antd 6's scroll locker calls on
every dialog mount — each throw emitted as a `jsdomError` carrying a full React
stack, until the suite's real output is unreadable.

A generated pair now gets `testTimeout`/`hookTimeout` at 30s, a
`test/vitest.setup.ts` copied from the etalon's shape with
`configure({ asyncUtilTimeout: 10_000 })`, and the three jsdom shims. The
`matchMedia` stub answers `(min-width: N)` by comparing against a real desktop
width rather than saying `false` to everything, so a test that cares about the
phone layout can replace it and decide, instead of inheriting an answer. The
setup also registers `afterEach(cleanup)`: vitest runs without injected globals,
so testing-library never registers its own, and every demo `demos.test.tsx`
mounted would otherwise stay mounted into environment teardown.

Generous, not permissive — a green test still resolves as fast as the state it
awaits.

## [0.55.3] — 2026-08-26

### A generated project promises only the image formats it can decode

The 0.55.2 publish failed its own test jobs: a generated project carrying cdn
did not pass `manage.py check`.

```
stapel_cdn.images.E004: STAPEL_CDN['ALLOWED_IMAGE_EXTENSIONS'] declares .bmp
allowed, but this libvips build has no loader for it
(bmpload, magickload not registered)
```

The runner is right and the check is right. stapel-cdn 0.10 made libvips the
ONE decoder on the image path — the upload guard and the processing pipeline
ask the same engine — so `ALLOWED_IMAGE_EXTENSIONS` is a promise a deployment
keeps or breaks, and E004 is boot-fatal about the gap rather than letting it
surface as `error.503.image_decoder_unavailable` on somebody's avatar. What
was wrong is the value: **libvips has no native BMP reader at all**. `.bmp`
decodes only through the optional ImageMagick module, which the
`pyvips[binary]` wheels (CI, and any laptop without an apt libvips) do not
carry. Measured 2026-08-26 in `python:3.12-slim` + `pip install pyvips[binary]`
(libvips 8.18.6, linux/amd64): `jpegload`, `pngload`, `gifload`, `webpload`,
`heifload`, `tiffload`, `svgload` registered; `bmpload`, `magickload`,
`jxlload`, `jp2kload` not — and an AVIF round-trip succeeds.

So the scaffold states its own honest set instead of inheriting one
(`_module_config.WEB_IMAGE_EXTENSIONS`):

    .jpg .jpeg .png .gif .webp .avif .heic .heif

`.bmp` is out for the decoder reason above, `.tif`/`.tiff` for the product one
(not a web delivery format). `.avif` is in: it rides `heifload`, which the
wheel does register, and it is what iOS and every modern browser now emit
beside HEIC. Applied per KEY by `inject_decodable_image_extensions`, in
`create_project` *and* in `scaffold_service` (so `stapel-new-service
--stapel-apps stapel_cdn` gets it too) — an explicit `--module-config` value is
never narrowed, because the generated Dockerfile's libvips does read more.

The generated `settings/base.py` carries the reason and the widening recipe
beside the value: which package each further format needs, and that
`manage.py check` will name anything the build cannot honour.

Also: `known_config_keys` now unions `SCAFFOLD_INJECTED_KEYS` — the keys the
generator supplies itself that a library has not declared as a capability axis
(cdn has no `ALLOWED_IMAGE_EXTENSIONS` axis today), which the second validation
pass would otherwise refuse.

Gated by two tests: the rendered settings (unit), and — under
`STAPEL_TEST_STRICT_SIBLINGS=1`, on the plain `pyvips[binary]` the `test`
extra installs — a generated cdn monolith that boots, exits `manage.py check`
clean, and whose `decoders.undecodable_allowed_extensions()` (the same
predicate E004 and the runtime 503 both call) comes back empty.

**Upstream note:** the defect is in stapel-cdn's own `conf.py` default, which
ships `.bmp` in `ALLOWED_IMAGE_EXTENSIONS` — a format its only decoder cannot
read without an optional ImageMagick module nobody's stock install has. Any
host project that does not override it hits E004 on a plain `pip install
stapel-cdn[images]`. The scaffold override above fixes every project this tool
generates; the library default is stapel-cdn's to fix.

## [0.55.2] — 2026-08-26

### SCHEMA001 stops warning on the correct state

`stapel-api-lint`'s SCHEMA001 demanded that `docs/schema.json`'s
`info.version` equal the package version — and all 24 sibling libs emit
`"0.0.0"` there on purpose, so the rule warned on every one of them. A gate
that reds the correct state is a gate the fleet learns to scroll past.

The convention, now written down and enforced instead of fought: `info.version`
is NOT the contract's version. Every lib leaves `SPECTACULAR_SETTINGS` unset in
its `_codegen_settings.py` so its emitted triad stays byte-identical to the
monolith aggregate's slice, and the aggregate runs on the drf-spectacular
defaults — `info.version: "0.0.0"` with an empty `info.title`. The version a
consumer pins lives in `pyproject.toml` and in the pair's `manifest.json`
(`backend.contract`).

SCHEMA001 therefore now fires on divergence *from* the convention:

* `info.version == "0.0.0"` **and** `info.title == ""` — clean, no finding;
* a lib that writes its package version into `info.version` (`package=` /
  `version=` passed to `get_spectacular_settings`) — flagged, with the
  convention named in the message and the fix stated: drop those kwargs and
  re-emit. This is a real defect, not a cosmetic one — the slice stops
  matching the aggregate byte-for-byte, which is what the per-lib triad exists
  to guarantee;
* anything else (a stale hand-set version, a title beside the placeholder) —
  still flagged, as before.

Still a warning, still promoted by `--strict`. Documented in the linter's
module docstring, the README rule table, `stapel_tools.codegen`'s emitter
docstring, and the `stapel-new-library` MODULE.md template (the scaffold has
no `_codegen_settings.py` of its own yet — geo is the etalon a module copies
when it grows a contract harness, and the template now says what to leave
unset when it does).

## [0.55.1] — 2026-08-26

### What 0.55.0's own CI caught, which is the point of having one

0.55.0 was tagged and never published: its e2e job failed on the generated
frontend, and its test job failed on a test that had never run before. Both
failures are the same class this release is about — code that is correct
against a sibling's WORKING TREE and wrong against what is published — so they
are fixed here rather than waved through.

* **`mode` is required on the published shell.** `@stapel/shell-react` 0.6.0
  declares `readonly mode: ThemeMode` for both `AppShell` and `PublicShell`
  (verified against the 0.6.0 tarball's `.d.ts`), and the optional-`mode` +
  `staff` shell exists only in the stapel-react checkout. Dropping `mode` and
  adding `staff` therefore generated a project that does not typecheck. Both
  emissions now hang off `FRONTEND_SHELL_SELF_THEMING_FLOOR` and the
  `FRONTEND_SHELL_REACT_VERSION` pin: below the floor a generated container
  keeps `mode="light"` and no `staff`; raise the pin to the release that ships
  them and the container changes shape in the same commit. Proven by building
  the generated frontend against the real registry, which is the step that
  failed.
* **A generated project carrying cdn does not boot without libvips.**
  `stapel_cdn.images.E001` fails `manage.py check`, and the URL-resolution test
  that boots a generated project for real had been SKIPPING on every CI run
  this repo ever had — the `importorskip` this release replaced. Its first
  actual run on a runner found it. `pyvips[binary]` is now declared in the
  `test` extra and named in the test's `requires(...)`.

## [0.55.0] — 2026-08-26 (tagged, not published — see 0.55.1)

### `stapel-sibling-lint` — three red releases in one night, as a machine check

On 2026-08-24 stapel-chat 0.5.0, stapel-core 0.44.0 and stapel-notifications
0.17.0 all shipped, all failed on the runner at test *setup* with
`ModuleNotFoundError`, and all were re-released the next morning with no
product change. One defect, three costumes:

* **a plain import** — chat's `tests/test_moderation_seam.py` reached for
  `stapel_moderation` inside a fixture and `tests/test_attachments.py` for
  `stapel_cdn` inside a helper. Neither was named in `pyproject.toml`. Depth is
  the trick: the imports that bit were inside functions and `try` blocks, where
  a module-header grep does not look;
* **a settings string** — core's `override_settings(INSTALLED_APPS=
  ["stapel_realtime"])`. No `import` token on the line, and it imports:
  `setting_changed` reloads the app registry and loads every label for real;
* **a skip that never ran** — `pytest.importorskip("stapel_cdn")`. It never
  failed, and on CI it never ran either, so two cross-module agreement tests
  had been asserting nothing for months without reddening anything.

The fleet develops in ONE virtualenv holding every sibling, so nothing in a
repo can disagree with an undeclared import: pytest is happy, ruff is happy,
review is happy, because a reader cannot see an absence. The new linter reads
for that absence — SIB001 (undeclared import, at any depth), SIB002
(`INSTALLED_APPS`), SIB003 (`importorskip`), SIB004 (a declared sibling behind
a skip guard with no strict flag in CI), SIB005 (a `test` extra nothing uses),
SIB006 (a foreign-owned key in the committed `docs/errors.json` — the same
class one step downstream, where the codegen publishes whatever the generating
venv happened to have installed).

Verified against the repositories it was written from, and those runs are the
fixtures under `tests/fixtures/siblings/`: chat at `v0.5.0` flags 10 errors
(three distributions, none declared) and at HEAD is clean; core at HEAD gives
SIB001 x9 + the SIB002 settings string; notifications at HEAD gives SIB001 x3
(`stapel_translate`, still live) + SIB004 (a declared `stapel_realtime` behind
`importorskip`, in a repo whose workflows set no strict flag).

**The `STAPEL_TEST_STRICT_SIBLINGS` contract**, documented for libs and
scaffolded by `stapel-new-library`: a DECLARED sibling that is not installed is
a FAILURE, never a skip. Unset — a laptop, a fork — it skips with a named
reason; set (CI, always) it fails, because CI installed `.[test]` two steps
earlier and a skip there is the install step lying. Generated libraries now get
the `test` extra, `tests/siblings.py` (`STRICT` / `installed` / `requires`), a
CI step that installs the extra, a `stapel-sibling-lint` step and the flag on
the pytest step.

Dogfooded first: this repo's own suite imported six siblings and declared none
of them, and its three `importorskip`s had therefore never run on any CI run it
has ever had. `pyproject.toml` now carries the `test` extra, `make check` runs
`sibling-lint`, and both workflows install `.[test]` and set the strict flag.

### The e2e installs versions that EXIST on npm

The 0.54.0 publish died on `No matching version found for
@stapel/auth-react@^0.16.1`. Nothing was wrong with the pin: it MIRRORS the
sibling stapel-react checkout, which had 0.16.1 in its tree with the publish
still pending, and npm had 0.16.0. The e2e job is the one consumer for which a
version living only in a working tree does not exist.

`scripts/e2e_npm_pins.py` now runs before `npm install` in both workflows. For
every `@stapel/*` dependency of the generated frontend it asks the registry
whether anything satisfies the declared range — the same resolution
`npm install` performs — keeps the range when something does, and falls back to
the newest published version when nothing does. Every fallback is LISTED by
name (`mirror ^0.16.1, npm has 0.16.0`), because "the pair has not shipped yet"
has to be a line in the log rather than a surprise for whoever generates a
project next; `--strict` turns the listing into a failure for the release path,
where a mirror ahead of npm means the publish order is wrong. Third-party
ranges are never rewritten: a miss on react or vite is a real defect, not a
publish-order fact. As of this release the mirrors ahead of npm are
`@stapel/auth-react` (0.16.1 vs 0.16.0), `@stapel/listings-react` (0.5.0 vs
0.4.0) and `@stapel/search-react` (0.5.0 vs 0.4.0).

### The generated shell stops guessing the theme and starts being told who is staff

`_frontend_templates` emitted `<AppShell nav={RESOLVED_NAV} mode="light" />`
(and the storefront's `<PublicShell nav={nav} mode="light" />`). Both are a
generator answering a question it cannot know: the shell follows the document's
live `data-theme` through `SkinTheme`, and a pinned side overrides the reader's
own setting on every dark deployment. Both `mode` props are gone.

The opposite defect sat next to it: `<AppShell/>` reads no session by design
(the rule that keeps `resolveNav` pure), and takes `staff` from the container —
so with nothing passed, `staff` defaulted to false and the admin section was
drawn switched-off for the very staff who own it. When auth is wired, routes.tsx
now emits a local `AppChrome` that reads `useAuthSessionState()` and hands down
`staff={user?.is_staff === true}` — the same field `AdminGate` refuses on, so
the menu and the screen cannot disagree. Without an auth pair there is no staff
fact to read, and the shell's own default is then the honest answer.

## [0.54.0] — 2026-08-24

### The scaffold ships a FEATURE — `stapel-new-react-lib` reaches etalon parity

> `--no-skin` is the new opt-out. Every pair scaffolded from now on carries a
> default AntD skin, ru/es locales and a nav entry; a pair that is genuinely
> headless by design (billing, calendar, recordings) asks for that with the
> flag and says why in its MODULE.md.

The shared-layer audit measured it: *"every freshly scaffolded pair is
'headless shipped, feature not shipped' on day one"*. The scaffold emitted the
layer a host REPLACES and nothing of the layer a host SHIPS — no `src/default/`,
no `./default` export, no antd peer, `en` only, no guidelines, no phone demo —
so every new pair began as a §54 violation by construction and each one
rediscovered the fix by hand.

`file_plan` now emits, from the auth-react etalon:

* `src/default/<Camel>Panel.tsx` + `index.ts` + `types.ts` — a themed skeleton
  through `SkinTheme` (`@stapel/tokens-antd/skin`), never a local
  `ConfigProvider` (which forks the token bridge per pair) and never a
  `mode = "light"` default (which ignores a dark host);
* `./default`, `./i18n/ru`, `./i18n/es` export subpaths + their own size-limit
  budgets, `antd >=5.20.0 <7` / `@stapel/tokens-antd >=0.6.0` peers, and the
  `tsc` build that emits exactly the paths the exports map names;
* `src/i18n/{ru,es}.ts` with the en floor registered UNDER the locale (a missing
  key degrades to English, never to a raw key) and `test/i18nParity.test.ts` —
  the gate 11 of 19 live pairs had no version of;
* `src/nav/manifest.ts` with a REAL entry (a skinned pair has a component the
  entry can name) rendered from the same dict as `nav-manifest.json`, so
  declaration and projection cannot disagree before `pnpm gen:nav` runs;
* `docs/guidelines.md`, `demo/<Name>Skin.demo.tsx` with a `viewport: "phone"`
  variant, and a harness themed by the same `SkinTheme` a host gets.

Fixed on the way: the demo harness spoke the PRE-§68 token roles (`card-bg`,
`color-text-primary`, …), so a scaffolded pair's demos did not typecheck at
all. Proven end to end — `tsc -p tsconfig.json`, `tsc -p tsconfig.demo.json`
and `vitest run` over a freshly scaffolded probe package are green.

### The nav contract stops failing silently

Three defects, all invisible in the output:

* **`admin.root` was declared by nobody.** gdpr's `admin.privacy` and video's
  `admin.usage` hang from it, so every generated container dropped them as
  orphans — two real screens, no log line. Both containers now declare the root
  when (and only when) a selected pair hangs a screen from it, write its landing
  page, and gate the subtree with `<AdminGate/>`: `user.is_staff` from the auth
  session, REFUSING BY NAME rather than hiding (a menu entry that vanishes
  teaches nobody the screen exists).
* **`requiresAuth` was emitted and read by nothing.** "/app" and "/account" are
  gated as subtrees, but an absolute-path entry is their sibling:
  `auth.qr_confirm` (`surface: "public"`, `requiresAuth: true`) mounted for
  anonymous visitors. Per-route now: `<ProtectedRoute>` in the monolith, inside
  `<MemberGate/>` on a storefront — never "/login", which is where the gate
  redirects.
* **The member container resolved with no audience.** `resolveNav`'s audience
  argument is optional and its default does not filter, so a guest mandate
  inside "/app" got every member entry. The generated `nav.generated.ts` now
  calls `resolveMemberNav`/`resolvePublicNav` — the audience is in the name.

`validate_nav_entries` refuses at GENERATION time: an icon outside the shell's
registry (16 names — an unknown one renders a generic glyph with no error), a
`parentId` no registered pair and no container root declares, and `route.index`
— which is hereby DROPPED from the contract rather than half-implemented
(`resolveNav` copies `route` opaque, the shell's `matchesLocation` ignores it,
and the container decides its own section index).

### The listing composer is wired, not a placeholder

`NAV_ENTRY_MOUNTS["listings.compose"]` emitted a named gap, which meant the
scripted storefront had no way to list anything — a library, not an app. The
cross-pair page is now generated: `useCategoryFeatures` + `<CategoryPickerField>`
from categories-react, ONE `useUploadQueue` bag drawn by `<MediaGalleryField>`
from cdn-react (two queues publish an empty `images_draft` while the photos sit
on screen). The one slot it cannot fill — `renderLocationPicker`, which needs a
geo pair that does not exist — is NAMED on the page instead of silently asking a
seller for a latitude. A selection missing a member pair still falls back to the
placeholder, naming which prop belongs to which pair.

`forms` joins `FRONTEND_REACT_LIBS` (its backend has been selectable since the
module landed; the frontend registry had no entry, so `--modules forms` shipped
a headless backend). No nav mirror yet: forms-react publishes no
`nav-manifest.json`, and a mirror claiming entries a package does not publish is
what the drift gate exists to refuse.

### Composite frontend presets — `--preset shop|classified|booking|social`

A composite backend mounts no URLs: its `preset.py` is a named set of modules.
Its frontend counterpart is the same thing one layer up, so there is no
`@stapel/shop-react` to build — there is `FRONTEND_COMPOSITES`, a table of
member pairs whose expansion (pairs, baked nav bundle with the container roots,
container pages) is COMPUTED by the same functions the generator uses. Members
the fleet has no react pair for yet are printed by name — classified names geo,
moderation and currencies rather than shipping without them quietly.

### Browser WebSocket origins reach the generated stack

stapel-core 0.44.2 gates the cookie branch of a WebSocket handshake on an origin
allowlist and FAILS CLOSED (an empty list refuses every handshake, close 4403;
`manage.py check` reports `stapel_core.jwt.E001`). A cookie is ambient
authority — the browser attaches it to a handshake started by any page, and
sockets have neither the same-origin policy nor CORS in front of them. So:
`STAPEL_WS_ALLOWED_ORIGINS` in both `.env.local` presets and both prod env
templates (with the SPA-on-another-site `JWT_COOKIE_SAMESITE=None` + Secure pair
documented beside it), a working default in `dev.py` for a native Vite run, and
the core pin moved to 0.44.2 — a scaffold may not emit a setting the library it
pins has never heard of.

## [0.53.0] — 2026-08-24

### `stapel-authz-lint` — the "credentials verified, authorization never asked" gate

> **A NEW GATE. `stapel-verify` now composes a sixteenth linter, so a project
> that upgrades stapel-tools can go red on code that has not changed.** All
> five rules are new checks, four of them errors. If your pipeline turns red
> on this upgrade, read the finding before waiving it: the class it describes
> shipped a full authentication bypass in stapel-core and stayed live for
> months, invisible to a green suite. Per-rule escape is
> `# noqa: AUTHZ00N <reason>` on the line; per-surface escape is the
> `stapel-lint.toml` profile (`[waivers]` takes a rule id and a written
> reason). Both leave a record; skimming past a red wall does not.

On 2026-08-24 stapel-core shipped five security releases (0.38.0-0.43.0) for
what turned out to be **one defect class wearing five costumes**: every place
where the code proved *who you are* and then never asked *what you may have*.

`JWTCookieLoginView` is the admin login view — its template is
`admin/login.html`, its redirect target is the admin index — but it named no
`authentication_form`, so Django used the plain `AuthenticationForm`, which
checks `is_active` and nothing else. `form_valid()` then called `login()`,
`create_tokens()` and `set_jwt_cookies()` with no staff check anywhere in its
body. Any active account's own password minted a fleet-wide JWT pair, walking
past a consumer's password-login gate, its lockout service, its TOTP step-up
and its tracked-session creation. The file *did* read `is_staff` three times.
All three sat in `dispatch()`'s already-authenticated branch, none on the
minting path — so every file-level grep called it safe. It was not.

Four more of the same shape, one layer down: a refresh endpoint re-minting
from a presented token's own up-to-7-day-old claims; two `get_user()`
overrides that silently dropped the `user_can_authenticate()` check
`ModelBackend.get_user` performs, so a deactivated account kept a live session
for the life of its session cookie; and both blacklists writing through
`django.core.cache.cache`, whose real key comes from the *deployment's*
`KEY_PREFIX` — `auth` wrote `auth:1:jwt_blacklist:<jti>` while `profiles` read
`stapel_profiles:1:jwt_blacklist:<jti>`, making "log out everywhere" a
per-service illusion for months. That last one is what made the login bypass
unrecoverable while it was live.

Every one of the five was found by a human reading code. **None was found by a
test**, because each component's own suite was green: the login view did log
people in, the refresh endpoint did return a token, the blacklist did
blacklist. The defect lived in what was *not* written, and nothing in the
fleet read for absence. Five point fixes would have closed five instances of a
class that has more instances. This is the class, as a machine check.

| rule | level | what it holds |
|---|---|---|
| AUTHZ001 | error | a `LoginView` subclass defining `form_valid` must either name an `Admin*` authentication form (class attribute or `get_form_class`) or read authorization inside `form_valid`'s **own body** — a gate in a sibling method is not on the minting path |
| AUTHZ002 | error | `create_tokens`/`set_jwt_cookies` in a function that also calls `login()` or `form.get_user()` needs an authorization read **earlier in the same function**. A token outlives the request that minted it, so a check after the mint is a log line |
| AUTHZ003 | warning | an explicit `refresh_access_token(x, None)`. Since core 0.39.0 the django-layer default IS the database loader, so the bare call is the safe form and only the typed-out `None` is flagged: it has to be a decision, not an omission |
| AUTHZ004 | error | a `get_user()` **method** override returning `objects.get()` needs an `is_active`/`user_can_authenticate` check — it resolves `request.user` on every request after the one that authenticated |
| AUTHZ005 | error | a revocation/blacklist entry read or written through `django.core.cache.cache` instead of `stapel_core.core.revocation_store.revocation_cache()` |

**The control that proves the rules are not inverted.** Run against
`stapel-core` at `v0.37.0` — the last tag before the fixes — the linter reports
12 errors: AUTHZ001 and AUTHZ002 on `django/jwt/login_views.py`, AUTHZ004 on
both `backends.py` and `session.py`, and AUTHZ005 on all four
`TokenBlacklist` methods plus the three user-blacklist functions. Run against
`HEAD` (0.43.0) it reports **zero errors**. A rule that fires on the remedy
and not on the defect is inverted, and this pair is the assertion that it is
not; `tests/test_authz_lint.py` carries the same pair as a fixture so it
cannot rot.

50 tests. Every rule ships a positive (the defect as it was actually written),
a negative (the shape core actually shipped as the fix), and at least one
**near-miss** — because a security linter that cries wolf gets suppressed and
is then strictly worse than no linter. The near-misses are drawn from the
fleet, not invented: `self.client.login(...)` in a test suite is not
`django.contrib.auth.login`; `SSOLoginView(APIView)` in stapel-auth is named
like a login view and is not one; a `cache.clear()` under a docstring that
mentions the blacklist is not a revocation write; and a `LoginView` subclass
whose only `is_staff` reads sit in `dispatch()` **must** fire, since that is
the shipped defect verbatim.

**What the rules cannot catch, on the record** (README carries the full list):
recognition is syntactic — no import resolution, no cross-module call graph,
no dataflow — so an authorization helper imported from another module reads as
absence, while a same-module predicate is followed one fixed-point hop.
AUTHZ001 keys on a base class name ending in `LoginView`, so a credential view
on `APIView` is invisible to it. AUTHZ002 stays silent on a mint with no
credential call in the same function (a DRF password-reset/OTP flow is the
intended grant, ~15 such sites in stapel-auth), which also means a bypass
split across two functions is not seen. "Before the mint" is source order, not
execution order. AUTHZ003 sees a literal `None`, never a runtime value.
AUTHZ004 covers `objects.get()`, not `filter().first()` or a manager method.
AUTHZ005 reads the enclosing function/class and the call's own identifiers and
strings — deliberately not prose — and does not follow a raw django_redis
handle taken through `cache.client`.

### Also

- `stapel-verify`'s composed roster is 16 linters; `stapel-authz-lint`
  declares surface `python`, so `stapel-lint.toml`'s existing switch governs
  it like every other Python-surface gate.
- New console script `stapel-authz-lint`.
- `# noqa:` parsing in this linter reads the first token of each
  comma-separated part, so `# noqa: AUTHZ001 - storefront login, not admin`
  suppresses. Every one of these rules asks for a written reason; a parser
  that then refused to read the line would be arguing with its own advice.
- The generated `AGENTS.md` names the AUTHZ codes in its verify section.

## [0.52.0] — 2026-08-24

### `stapel-disk` — a lifecycle for build/disk, because the machine died twice

Twice in one night the fleet's workstation reached **0 bytes free**. Nothing
failed in a way anyone could act on: an in-flight image build died mid-layer
on an opaque `ENOSPC`, the OrbStack daemon dropped its socket, and from then
on *every* shell tool on the host returned an EOF. Measured immediately after:

| | total | active | reclaimable |
| --- | --- | --- | --- |
| local volumes | 205 | 18 | **27.9 GB (93%)** |
| images | 99 | 30 | 12.4 GB (62%) |

The obvious hypothesis was that studio's e2e leaks its per-project resources,
and it does — `studio-vol-e2e-2f3f7a8c` and six `studio-vol-smoke-*` volumes,
each with its `studio-net-*` bridge, still on the engine weeks after the runs
that made them. But that is 13 objects, not 27.9 GB. The bulk was elsewhere and
had the same shape: **131 anonymous PostgreSQL data directories (~45 MB each)
created inside one 72-hour window**, by a migration-test script that starts
`postgres:10` and tears it down with `docker rm -f` *without* `-v`. The image
declares `VOLUME /var/lib/postgresql/data`, so every single run left a PGDATA
behind. Same defect, two sites: something creates a docker resource and nothing
owns its death.

A cleanup command would have bought one night. This is the mechanism instead,
and it lives in stapel-tools because it applies to everything in the fleet that
builds an image.

**`stapel-disk guard`** — preflight. It runs *before* the build, compares free
space against a threshold (`--min-free-gb`, `STAPEL_DISK_MIN_FREE_GB`, default
15 GiB) and refuses with the free space, the threshold, the shortfall and the
exact reclaim command. A refusal you can act on beats an ENOSPC at layer 7 of 9.

**`stapel-disk reclaim`** — tiered, and explicit about its limits. Tier 1
(always, safe to automate): build cache, dangling images, stopped containers.
Tier 2 (`--images`, opt-in): unreferenced images. **Volumes: never.**
`docker volume prune` and `docker system prune --volumes` are refused by this
tool at every tier, and the refusal says why — the studio's `project-repos`,
every stack's `db-data` and the snapshot volumes are named volumes, and a
blanket prune deletes the owner's data while reporting it as reclaimed space.
A test asserts that no argv this command can emit is capable of removing a
volume, so the rule cannot be relaxed by accident.

**`stapel-disk reap`** — the actual leak fix. It removes only resources that
*identify themselves* as throwaway: the `stapel.ephemeral=true` label, or an
explicit name pattern for the pre-label generation. Two guards stand in front
of the owner's data — a pattern needs four literal characters before its first
wildcard (`--pattern '*'` is refused, not obeyed), and every individual
resource is re-checked against the contract immediately before removal.
`--dry-run` lists the matches and the count of resources inspected and left
alone.

**`stapel-disk doctor`** — free space, reclaimable per tier, orphan counts. The
state made visible before it is fatal.

### Generated projects inherit the guard

`MONOLITH_MAKEFILE` gains `disk-guard` / `disk-doctor` and now carries guarded
`build` / `up` / `down` targets over `docker-compose.local.yml`;
`MINIMAL_MAKEFILE` gains `disk-guard` / `disk-doctor`. Both degrade loudly
rather than silently when `stapel-tools` is not installed. Threshold per
project: `make build DISK_MIN_FREE_GB=25`.


## [0.51.0] — 2026-08-24

### The library scaffold inherits the core serializer seam instead of embedding one

`stapel-core` 0.37.0 shipped `SerializerSeamMixin` + `StapelAPIView` in
`stapel_core.django.api.views` — extracted because twenty-three modules had
each hand-written the same four-line mixin, and the twenty-fourth definition
was this template. A scaffold that keeps embedding a copy is not a leftover:
it is the machine that mints the twenty-fifth. Generated `views.py` now does
`from stapel_core.django.api.views import StapelAPIView` and the example view
inherits it (`self.serialized_response(...)` in place of the hand-rolled
`StapelResponse(response_cls(dto))`); the scaffold's stapel-core floor moves
`>=0.29.0` -> `>=0.37.0`, since a library inheriting the primitive must pin
the release that ships it. Generated MODULE.md documents the inherited seam
rather than a local one. The existing end-to-end gate — the generated repo's
own pytest suite, run for real — covers the new import.

Removing the local copies from the 21 libraries that already carry one is a
separate wave; this change only stops new ones from being created.

### `stapel-api-lint` — the classifier the HTTP surface never had (§60, api-versioning.md §3)

Everything around this gate already existed and none of it closed the hole.
The contract pipeline emits `docs/schema.json` and checks it is byte-identical
to the committed copy — a drift gate says the file *changed*, never that the
change breaks a caller. Semver discipline says "minor = breaking" — but
nothing decided whether an HTTP diff was breaking; the author did, from
memory, at release time. Measured on the fleet the first time it ran:
`stapel-listings` 0.6.2 → 0.7.1 made `geohash_draft` required on POST and PUT
`/listings/api/v1/listings/` and shipped it with no upgrade record — six
request-side breaking changes nobody had a mechanism to notice.

API001 holds a breaking diff to BOTH a sufficient version bump and a
`docs/UPGRADE.json` `kind: api_change` record. API002 forbids reshaping `vN`
in place — `vN+1` goes beside the frozen one, and `urls.py` must still mount
`urls_vN.py`, because a version in the schema but not in the URLconf is
documented rather than served. API003 refuses to let a version disappear
before its `x-stapel-sunset` date, or with no sunset ever declared. SCHEMA001
(warning) reports `info.version` still carrying the drf-spectacular
placeholder `"0.0.0"` instead of the package version.

Required-status is read per direction, which is the difference between a gate
people use and one they route around: optional→required breaks a REQUEST,
required→optional breaks a RESPONSE, and the opposite flip on each side is a
strengthening that stays additive. Nullability reads the same way. Enums are
open by default (adding a value is additive); `x-stapel-closed-enum` marks the
role/permission/billing vocabularies where an old client's exhaustive mapping
is a security answer, not a display glitch.

No `oasdiff` dependency: the rule set is five bullets wide, and the point is
that the classifier agrees with a written policy rather than with another
tool's idea of compatibility. Composed into `stapel-verify` (15 linters now),
which forwards `--base-sha` as the schema baseline; on the `python` surface,
so a project that turns that surface off is ungated here on the record.


## [0.50.2] — 2026-08-24

`stapel-verify`'s closing line counted linters that never ran: "All clean
across 15 linters" printed directly under "9 of 15 linters did not run" is the
exact false reassurance the per-project lint profile (0.50.0) exists to
prevent — a legacy project with most surfaces on `native`/`off` would end its
gate on a sentence claiming coverage it did not have. The line now counts what
actually ran and says so: "All clean across 6 linters that ran".

## [0.50.1] — 2026-08-24

### SUR002 asks about the displaced symbol, not about one replacement's name

`stapel-core` publishes `HasWorkspaceMandate` and `HasWorkspaceMandateIfScoped`
with an identical `instead_of` list, and which of the two a view takes is a
decision the project makes per view: the strict class for a product view, the
scoped one for a library view a single-tenant host also mounts — that class's
own docstring says exactly that.

`check_instead_of` silenced per displacing ENTRY (`entry["name"] in
seen_names`), so a project that adopted the strict sibling and documented why
it refused the scoped one was still reported for the same two displaced
symbols, by the sibling it had correctly declined. Measured on meettoday: two
SUR002 errors on views that are right as written, whose only routes to green
were a weaker gate or a silenced rule — the two outcomes the SUR family exists
to avoid.

The unit is now the displaced symbol: a symbol is answered for when the
project uses ANY published replacement for it, and a finding names every
sibling it could have taken. The incident state — a project that has never
heard of any replacement — is unchanged and still fires.

## [0.50.0] — 2026-08-24

### A legacy project can be gated by ITS OWN linters — `stapel-lint.toml`

Every linter `stapel-verify` composes encodes a *stapel* contract. Against a
project stapel generated, all fourteen are fair. Against an **imported legacy
project** none of them is: a Django shop that never heard of `stapel_core`
trips R-codes, SWAP-codes and CFG-codes in the hundreds on its first commit,
not one of which describes a defect — they describe the project not being a
stapel project. A pipeline that runs the arsenal there produces exactly one
thing: a permanently red gate the operator learns to skim past, which is
strictly worse than no gate, because it is ungated *and* unrecorded.

New `stapel_tools.lint_profile`: a project-root `stapel-lint.toml` declaring
one **mode per surface** (`python`, `frontend`, `docs`, `i18n`, `deploy`) —
`stapel` (the arsenal), `native` (the project's OWN `ruff`/`eslint`/whatever
IS the gate) or `off`. An absent file means `stapel` everywhere, so every
generated project is bit-for-bit unaffected.

The unit is a surface, not a linter, because that is the unit an operator can
reason about: "this project's Python is gated by its own ruff" is a decision;
"ADO002 off, ADO003 on" is a configuration accident waiting to happen.
Individual rules stay reachable through `[waivers]`, one id at a time, each
with its written reason — the same shape as
`STAPEL_SECURITY_CHECK_WAIVERS`.

Three rules keep it from becoming a silent kill switch:

* `off` without a `reason`, `native` without a `command`, or a waiver with an
  empty reason **raises** `LintProfileError` — the gate stops and names the
  line. A malformed profile must not degrade into "run everything" (a
  surprise red wall) or "run nothing" (a silent ungating);
* every non-`stapel` surface still emits a report line carrying its mode and
  reason, in the human table and in `--json`, next to a `profile` block —
  what was *not* checked is as visible as what was, including a waiver that
  matched nothing this run;
* a `native` command is a shell command out of the tree under inspection, so
  it runs only under the new `--run-native` flag. Studio's sandbox passes it
  (it already runs the project's own `make controls` there); a bare local run
  of an untrusted checkout does not, and reports the surface as
  declared-but-not-run rather than green-by-omission.

A native gate's exit code is the verdict and its output tail is the evidence.
stapel deliberately does not parse another tool's format — the coder loop
never needed it to, it already reads `make controls` tails the same way.

`verify.COMPOSED_LINTERS` is now the declared, introspectable composition
order (`verify_project` asserts its own list against it), and every composed
linter must carry a surface in `LINTER_SURFACES` — a linter absent from that
map would run under a profile that says the surface is off, which is the one
failure mode a switch like this must not have.

### `assemble_scaffold`'s `check` gate finally sees 0.49.0's billing hatch

0.49.0 wrote `ALLOW_UNCONFIGURED_PAYMENT_PROVIDER=1` into the DEV env and
stopped there — but the `check` gate, the one place that proves a generated
tree boots at all, read `.env` only. A studio-generated monolith selecting
billing therefore still went `SCAFFOLDING -> FAILED` on E104: the exact
outcome 0.49.0 set out to prevent, at the exact place it mattered.

The gate's env is now `.env` plus the keys `.env.local` **adds**. Additions
only, in that direction, because `.env.local` also re-declares `SECRET_KEY`
and `POSTGRES_PASSWORD` as committed dev placeholders, and letting those win
trades E104 for `stapel_core.prodguard` E001/E002 — a different false red,
not a fix. The prod guard is untouched: `.env`/`.env.example` carry no hatch,
so `deploy/check-env.sh` and a real prod boot behave exactly as before.

## [0.49.0] — 2026-08-24

### A generated project can select stapel-billing and still boot in dev

`stapel_billing.E104` is a deliberate prod guard: an unconfigured payment
provider must never boot silently and answer checkout/portal/cancel with
fabricated placeholders. The library was never the defect. The scaffold was:
a generated project that selected billing with no Stripe key could not even
run `manage.py check`/migrate locally, because E104 fires regardless of
environment — there was no dev/prod split for it to hide behind.

The fix lives entirely in env emission, not in stapel-billing. When billing
is selected with no `STRIPE_SECRET_KEY` supplied at generation time, the
generated DEV env opens stapel-billing's own escape hatch —
`ALLOW_UNCONFIGURED_PAYMENT_PROVIDER=1`, with a comment explaining it answers
with dev placeholders (W104) and must never leave a developer machine:
`.env.local` for monolith, `.env`/`.env.example` for minimal (one env file,
not a pair — the same file every other dev-only default here, SECRET_KEY and
DEBUG included, already relies on the operator to harden before
`DJANGO_ENV=prod`). The monolith/microservices PROD template
(`.env.example`) never carries that flag; it gets a commented
`# STRIPE_SECRET_KEY=` placeholder instead, so E104 still fires at prod boot
until the owner configures real keys. That split IS the prod guard.

### STAPEL_LIBS completeness: stapel-docs, stapel-forms, stapel-moderation

All three shipped with a full MODULE.md/CONFIG.MD/urls.py surface and a PyPI
release but were never added to the registry `create_project`/
`assemble_scaffold` read from — a selection naming any of them silently fell
through `assemble_scaffold`'s "unknown lib" path instead of generating a
working project. Onboarded following the second-wave shape: each mounts bare
at its own `/<mod>/` prefix (v1 canon already baked into each library's own
root URLconf), `forms` carries the same hard `attributes` dependency its
pyproject.toml already declares (a form's schema IS a list of
stapel-attributes FeatureDefs), `docs`/`moderation` depend on stapel-core
only. Their GDPR participation (`docs` on the erasure-request protocol,
`forms`/`moderation` on the in-process `GDPRProvider`) needed no registry
changes at all — `stapel_tools/_gdpr_owners.py` reads it straight from each
library's own source, by design (0.48.0).

## [0.48.0] — 2026-08-24

### The gdpr data-owner inventory is derived from the selection, not asked for

stapel-studio's scaffold-assembly task calls
`assemble_scaffold(slug, libs=[...], config=None)` — no per-module config,
because a static assembler has nobody to ask. Every selection containing
`gdpr` died there on this repo's own gate: "required module settings are
missing", `STAPEL_GDPR["DATA_OWNERS"]`. That is one e2e wall and seven unit
failures in studio, and the fault was here: the generator installed
`stapel_gdpr` plus the libraries that hold the data, and emitted no inventory
tying them together.

Asking the caller was never the answer. `DATA_OWNERS` is not an opinion — it
is a FACT about the selection, and every participating library already
publishes it. `stapel_tools/_gdpr_owners.py` reads it, and
`create_project`/`new_service` emit the map plus a dated
`DATA_OWNERS_VERSION` into the generated settings, with the law written
beside it: an erasure is only proven complete when every store listed there
returns a receipt, so a store missing from the map is never asked, never
waited for, and the closure reports DELETED while the data is still on disk.

Two participation shapes are read, both from the library itself:

* the erasure-request contract — a library that ships
  `schemas/consumes/gdpr.erasure.requested.json` and declares
  `OWNER`/`SUBJECT_TYPES` (or `GDPR_OWNER`/`GDPR_SUBJECT_TYPES`) in its
  `erasure.py`/`gdpr.py`. This is ADO005's detection exactly, IMPORTED from
  `adoption_lint` rather than forked, so the linter and the generator can
  never disagree about who an owner is;
* the in-process `GDPRProvider` — `apps.py` registers a provider class of the
  library's own and the class carries a `section`. Leaving these out is not a
  cosmetic gap: stapel-gdpr's `gdpr.E002` refuses to boot on a registered
  provider absent from the map, so a `gdpr`+`chat` project would have failed
  `manage.py check` just as surely as an empty inventory did.

Where both apply the erasure contract wins — it carries the real subject
types, and the two declarations agree on the name by design. Names are the
libraries' own and are not package names: `stapel-cdn` answers to `media`,
`stapel-profiles` to `profile`, `stapel-translate` to `translations`. A
library that plainly participates but whose constant cannot be read is a hard
generation failure naming that library — never a guessed name and never an
example placeholder, because a guessed owner is the silent-retention defect
with a different spelling. `stapel-gdpr` itself is not an owner: it registers
whatever `GDPR_PROVIDERS` names, not a store of its own.

A caller-supplied inventory is never overwritten — an operator who wrote one
knows about stores this generator cannot see (search indexes, warehouses,
third-party processors). Nothing is invented when no selected library owns a
store: an empty map is the exact state `gdpr.E001` fires on, so the
required-settings gate still refuses, with the fix attached.

`tests/test_assemble_scaffold.py::TestFourLibProof` now runs with no config
at all — studio's call shape — and its `manage.py check` gate is what proves
the derived map right.

## [0.47.0] — 2026-08-23

### Nav mirror drift closed, gdpr-react and video-react registered

`tests/test_nav_manifest_sync.py::test_the_live_mirror_is_in_sync` had gone
red: `create_project.FRONTEND_REACT_LIBS` pinned nine published pairs behind
the wave that actually shipped. Every pin now matches the live npm registry
— billing 0.7.1→0.8.0, categories/chat/listings/reviews 0.2.0→0.3.1,
cdn/search 0.2.0→0.3.0, `FRONTEND_SHELL_REACT_VERSION` 0.5.0→0.6.0,
`FRONTEND_REACT_CORE_DEPS["@stapel/core"]` 0.15.0→0.17.0, and
`FRONTEND_IMAGE_VERSION` 0.2.0→0.3.0 (auth, attributes, calendar,
notifications, profiles, recordings and workspaces were already current).

Two new pairs join the registry, each with its nav mirror copied
byte-exact from the sibling `stapel-react` checkout's `nav-manifest.json`:
`gdpr-react` 0.1.0 (`account.privacy` under the member account menu,
`admin.privacy` under the admin menu) and `video-react` 0.1.0
(`admin.usage`, call-metering usage under the admin menu). Their `default`
components (`PrivacyPane`, `PrivacyAdminPane`, `ScopeUsagePane`) take no
required props, so each gets an empty `NAV_ENTRY_MOUNTS` recipe alongside
the other zero-prop screens.

Registering gdpr as a react-paired module retired the two frontend-scaffold
regression tests that used it as their "genuinely pairless" example
(`test_only_non_react_paired_libs_produce_the_prior_clean_shell`,
`test_no_flags_no_nav_module_scaffold_is_byte_identical_to_app_tsx`) —
both now use `currencies`, which still has an empty `requires` list and no
published react pair, matching the precedent already set when
`attributes-react` shipped and the pairless-lib test moved to `vault`.

## [0.46.1] — 2026-08-23

### nginx cache/frontend-delivery lints now read `*.inc` route tables

The storefront wave's fleet read found the actual defect: `stapel-nginx-
cache-lint` and `stapel-frontend-delivery-lint` both globbed only
`service-configs/nginx*/**/*.conf(.template)`, so a fleet whose whole route
table lives in an `include`d fragment (a client fleet's
`service-configs/nginx/locations.inc`, shared verbatim between the plain-HTTP
and TLS server blocks) got ZERO coverage from either gate — every `location`
block, and the cache-canon headers on it, went unread. `stapel-verify`
printed "no issues" having checked nothing.

Both lints' `CONF_GLOBS` now include `*.inc` alongside `*.conf` and
`*.conf.template`, for every layout each gate already recognized
(`service-configs/nginx*/`, `nginx/`, and — frontend-delivery-lint only —
`deploy/nginx/`). The overlapping portion of the two gates' globs, which had
already drifted apart once (frontend-delivery-lint gained `deploy/nginx/`
and cache-lint gained the bare `nginx/*.conf` meettoday fix, each without
the other), is now one constant: `frontend_delivery_lint.CONF_GLOBS` is
`nginx_cache_lint.CONF_GLOBS` extended with the `deploy/nginx/` patterns,
instead of a second hand-maintained copy.

Verified read-only against a real client fleet checkout: `locations.inc`
and `proxy-headers.inc` are now discovered (previously only `nginx.conf`
was), 17 `location` blocks in `locations.inc` are now parsed and checked by
`stapel-nginx-cache-lint` (previously 0) — the fleet's cache canon is
compliant, so the finding count is 0 both before and after, but the "0" now
means read-and-clean rather than never-read.

## [0.46.0] — 2026-08-23

### ADO005 — an installed gdpr data owner that cannot answer an erasure

The deletion-lifecycle wave (`tasks/deletion-lifecycle-design.md` §2) built
one machine: an erasure request fans out to every declared data owner, and
the request is only DELETED when each owner sends a receipt. The inventory
that started the wave found the failure mode this gate exists for — two
owner libraries installed, migrated and healthy in a live fleet, and
unreachable: nothing in their deploy ran a `consume_actions` process, and
one of them was absent from `STAPEL_GDPR["DATA_OWNERS"]` besides. Neither
gap fails a test, a boot, or a health probe. The first makes every request
against that owner run out its clock as a TIMEOUT; the second is worse —
the orchestrator never creates a part for an owner it does not know, so the
request reaches DELETED with that owner's rows untouched. A receipt for work
nobody did.

`stapel-adoption-lint` now reads the two obligations that make an installed
owner a reachable one, for every library in `INSTALLED_APPS` that ships
`schemas/consumes/gdpr.erasure.requested.json` (shipping the consume-contract
IS the declaration of participation — no second registry to keep in sync):

1. **A consumer runs in this service's deploy — when the service's actions
   ride a broker.** `STAPEL_COMM["ACTION_TRANSPORT"]` decides: in-process
   delivery (stapel-core's default, and every monolith) calls the handler in
   the emitting process, so no consumer process exists to look for and none
   is demanded. The toolchain's own e2e generated project was the first thing
   this rule met, and it was right to stay silent there. The compose fragment is
   located the way the rest of the toolchain locates deploy files: the
   project's own `docker-compose*.yml` for a monolith, and for a fleet
   service the sibling `<svc>.yml` next to `services.conf` — the layout
   `scripts/verify_boot_contract.sh` walks. A service's OWN fragment is the
   whole answer there; reading the root stack would let one service's
   consumer certify all the others. Commented lines do not count (the
   emitted fragment ships a commented `serve_functions` worker, and a gate
   satisfied by a comment is a gate that lies).
2. **The gdpr host lists the owner, with the subject types the library
   claims.** The owner name and its subject types are read from the
   library's own `erasure.py`/`gdpr.py` — `OWNER`/`SUBJECT_TYPES` or
   `GDPR_OWNER`/`GDPR_SUBJECT_TYPES`, both spellings live in the fleet —
   through the indirections real libraries use (`SUBJECT_TYPES` as a tuple
   of module constants, `OWNER = SomeProvider.section`). The name is never
   guessed from the package: `stapel-cdn` answers to `media`,
   `stapel-profiles` to `profile`. The host is this service when it installs
   `stapel_gdpr`, otherwise the sibling service in the same fleet that does.
   Both the 0.5.0 map and the legacy plain list (which means `["account"]`)
   are read, so an owner claiming `workspace` under a legacy list is a
   finding, not a silent pass.

Each half is skipped with a stderr note when its input is not discoverable —
no compose file at all, no service running `stapel_gdpr`, a computed
`STAPEL_GDPR`. Unreadable is not the same as missing, and this linter does
not invent findings out of what it could not parse.

Module docstring documents the rule and its parsing limits; `stapel-verify`
composes it unchanged (ADO-codes). Covered by 15 tests over a fixture fleet
(`services.conf` + `svc-app/` + `svc-app.yml` + a `svc-gdpr` host): missing
consumer, commented consumer, owner absent from `DATA_OWNERS`, subject type
not claimed, legacy list form, an owner name that differs from the package
name, both constant spellings, indirect constants, a non-owner module, the
monolith shape, and each skip note.

### The library CI template installs the extras its tests need

Two library releases were burned in one day (0.14.0 and 0.16.0 of two owner
libs) on the same defect: a beat-schedule test imported `celery`, CI had
installed the base package, and the suite failed at COLLECTION — green code,
red run, a version number spent to find out. The class fix belongs to the
template, not to the two repos that hit it:

* `_library_templates.CI_YML` — the install step no longer reads
  `pip install ".[all]" || pip install .`, which hid a genuine resolution
  failure behind its own fallback. It now asks `pyproject.toml` whether an
  `all` extra is declared and installs `.[all]` or `.` accordingly, so a
  broken extra fails loudly and a package without extras is unaffected.
* `_library_templates.TEST_PING` — the scaffold suite ships the documented
  pattern for the case that has no extra to install: a periodic callable is
  plain Python any scheduler can invoke, the `crontab` the factory builds is
  not, so the test guards it with `pytest.importorskip("celery")` rather
  than widening the package's runtime dependency to make a test pass.

**Existing libraries: re-sync your `ci.yml` install step.** The step is not
regenerated for you (most repos have since added steps of their own). Print
the canonical block and replace the `- name: Install` step with it:

```sh
python -c "from stapel_tools import _library_templates as T; \
  b = T.CI_YML.split('      # Optional dependencies')[1]; \
  print('      # Optional dependencies' + b.split('      - name: Lint')[0])"
```

Find the repos that still carry the old step:

```sh
grep -rl 'pip install .*\[all\]" || pip install' ~/Projects/stapel/*/.github/workflows/ci.yml
```

## [0.45.0] — 2026-08-23

### A public storefront container is now generated, not written by hand

`stapel-frontend-repo-init --surface public --pairs <csv>` writes the SOURCE
half of a split-repo frontend, next to the delivery half it already wrote.
Before this, a microservice fleet's frontend repository got a Dockerfile, a
publish script and a CI job — and not one line of React — so the container
(providers, route tree, access gate, nav resolution) was hand-written per
product, and every copy drifted from the pairs it mounted.

What it emits, and what each piece is FOR:

* `src/modules.tsx` — one runtime, one provider and one catalogue per pair,
  `baseUrl` `/<key>/api/v1/`, a single `<StapelProvider>` carrying every
  client. It reads an L0 pair (no client, no provider — `@stapel/attributes-react`,
  whose backend has no HTTP surface at all) off the registry's shape rather
  than off a flag someone has to set.
* `src/mandateSource.ts` — this container's `MandateSource`, so the mandate
  axis works without `@stapel/workspaces-react` (mounting the multi-tenant
  metaphor inside an anonymous marketplace to answer one boolean). Every arm
  of `MandateState` is produced by a named branch, and the arm that is NOT
  produced — `"guest"`, a principal a storefront has no way to be — says so in
  writing, because a reader has to tell "impossible here" from "forgotten".
* `src/nav.generated.ts` — the installed manifests plus the container's own
  `account.root`, resolved through `resolvePublicNav`/`resolveMemberNav`.
  Never `resolveNav(…, {audience})`: the option is optional and its default
  does not filter, so a container that forgets it mounts every member screen
  and every one of them answers 403.
* `src/routes.tsx` — `<PublicShell/>` as the layout route, public routes as
  siblings, `/account` and the absolute member routes inside `<MemberGate/>`.
* `src/MemberGate.tsx` — `matchMandate`'s five arms, each rendering something
  DIFFERENT and none of them nothing: `<Outlet/>`, `/login?next=`, a skeleton
  for the wait, and an explained error with a retry for the outage. An outage
  deliberately does not redirect to sign-in — bouncing someone to a login form
  because a backend hiccuped tells them they are logged out when they are not.
* `stapel.theme.json` + `gen:tokens`, `stapel.nav.json`, `reserved-paths.json`
  (never a bare module root — `location /listings` is a prefix match, and a
  bare rule makes a listing page answer JSON), `vite.config.ts` with the
  fleet's sub-surface proxy table, `eslint.config.js` that actually POINTS the
  two data-driven guardrails at data, and the dist-carrier delivery half.

**A declared screen the generator cannot honestly mount gets a page that NAMES
the absence** — the entry id, the component, the package and the exact prop
names — never a broken mount and never silence. Two things trigger it today:
a cross-pair slot (`ListingComposerPage` needs the chosen category's feature
schema, which lives in another pair; passing `[]` would not read as "not wired
yet", it would read as "this category has no attributes"), and a deployment
fact the generator was not told (`--doc-type`, the search doc type — a guessed
one sends every search to a type the backend refuses, from a page that looks
perfectly wired).

`--realtime` is a documented switch, not a silent one: there is no
`@stapel/realtime` package (stapel-realtime is a Python library; its browser
half is not built), so the flag turns on `@stapel/chat-react`'s OWN socket
transport and the generated README says exactly that. Without it the chat
runtime is given `realtime: { socketUrl: null }` — the WSGI answer, stated
rather than discovered.

### `stapel-new-react-lib` enrolls a pair in the nav contract from its first commit

The scaffold now writes `src/nav/manifest.ts` and `nav-manifest.json`, and
enrolls the pair in the root `gen:nav`/`gen:nav:check` aggregates —
`NAV_PACKAGES` and the enrollment are the same list, because the driver
rebuilds the monorepo's root aggregate on every run. Appending an invocation
while leaving the existing lists alone would make the aggregate depend on
which invocation ran last.

The entry list starts EMPTY, and that is the honest shape: a fresh pair ships
no `./default` subpath, so any entry would name a component that resolves at
the CONTAINER's import — two repositories from the mistake. The emitted JSON
is byte-identical to what `gen-nav-manifest.mjs` writes, so a freshly
scaffolded pair is already green under `pnpm gen:nav:check`; a scaffold whose
first act is to redden a gate is one everyone learns to run with the gate off.

### The nav-manifest drift gate was wired into nothing, and now is

`scripts/check_nav_manifest_sync.py` has been in this repo since the
scripted-navigation wave with no Makefile, no CI step and no hook calling it.
The mirror it guards had drifted accordingly: `@stapel/auth-react` pinned at
0.10.1 against a published 0.16.0, with `auth.qr_confirm` missing from the
mirror entirely — and every scaffolded project inherited the stale menu.

* `make check` (new `Makefile`) and a CI step now run it; it reads the sibling
  checkout through `$SIBLING_ROOT`, the same convention the generated `gen:*`
  invocations already spell as `${SIBLING_ROOT:-..}`.
* A registered pair whose real `nav-manifest.json` is MISSING is now a
  failure. It used to be a silent skip, which is the exact shape of the bug
  the gate exists for.

### Fixed: a CONFIG.MD recovery test that pinned a snapshot of another repo

`test_recovers_lib_list_core_first` asserted `["core"]` because core was the
only lib shipping a CONFIG.MD — and said so in a comment that added "the
mechanism generalizes the moment a lib ships its own". stapel-gdpr now does,
and the snapshot went red on a change in another repository. The assertion is
now the invariant (core first, the rest sorted, a lib comes back if and only
if it contributed a section), which cannot rot when the next lib ships one.

### `FRONTEND_REACT_LIBS` re-pinned against the live registry, and seven pairs added

`attributes`, `categories`, `cdn`, `chat`, `listings`, `reviews`, `search` join
the registry with their pinned minors and their nav mirrors; `auth` 0.10.1 →
0.16.0 (plus the `auth.qr_confirm` entry), `profiles` 0.12.0 → 0.18.2,
`notifications` 0.6.0 → 0.9.1, `billing`/`calendar`/`recordings`/`workspaces`
to their published minors, `@stapel/core` 0.11.0 → 0.15.0 (the mandate seam
and the repeated-query-key encoder the wave-4 pairs bind), `@stapel/shell-react`
0.2.0 → 0.5.0 (the minor that ships `PublicShell`; it is also published now,
so the ahead-of-npm note is gone), antd 6.6.1, tokens-antd 0.5.0,
react-router 7.18.2.

Consequences inside the monolith scaffold, both of which were latent bugs the
moment those pairs became registrable:

* an L0 pair no longer `KeyError`s the module registry — it contributes a
  catalogue and nothing else;
* the `cdn: <other>Runtime.client` stopgap (from the months when no
  `@stapel/cdn-react` existed) is not emitted when the real pair is selected,
  where it would have shadowed the real client with a borrowed one.

`NAV_ENTRY_MOUNTS` (`_frontend_templates`) is the one table saying how each
declared screen is mounted, read off the components' own prop interfaces; a
test asserts it covers every registered nav entry, so a new screen arrives as
a failing test rather than as a page saying "this build does not know about
me".

## [0.44.0] — 2026-08-22

### New gate: `stapel-exposure-lint` — a private client name must not reach a public tree

EXP001 (a private name in a tracked text file of a public `stapel-*` /
`@stapel/*` project) and EXP002 (in the message of a commit not yet on any
remote; `--commits`, what the library pre-push hook passes). The names are
private too, so the list lives outside every repository —
`~/.stapel/private-names` or `$STAPEL_PRIVATE_NAMES_FILE`; with no list the
lint notes that and checks nothing, because a runner without the file must
not pretend it did. A private project (a client fleet, a studio checkout) is
"not applicable", never a failure for containing its own name. Generated
artifacts are NOT skipped: `docs/schema.json` carries docstrings into the
published wheel, and that is the hit that matters.

Why a gate: ten public repos were found carrying a client's name in
changelogs, docstrings, test fixtures and one published schema, plus sixteen
commit messages that cannot be scrubbed without rewriting history — the
second such sweep. `stapel-verify` composes the lint; the library template's
`.githooks/pre-push` runs it with `--commits` when the tool is on PATH.

## [0.43.0] — 2026-08-22

### A generated service states its identity-trust mode instead of inheriting it

Two production outages on the same day (app.ironmemo.com, 2026-08-15..16; task
#349) were one defect: **stapel-core flipped two defaults in a minor release
and no product had ever stated them**, so every service silently changed mode
on a version bump.

* `JWT_CREATE_USERS_FROM_TOKEN` `True` → `False`. A valid token naming a
  `user_id` the local database had never seen stopped materialising the shadow
  row and started being refused as stale. Seven of eight services began
  answering 401 to every user who signed up after the deploy; the only trace
  was `JWT Auth Failed - User creation failed`, once per request.
* `SECURE_PROXY_SSL_HEADER` unconditional → opt-in behind
  `STAPEL_TRUST_PROXY_SSL_HEADER`. Behind the https proxy
  `request.build_absolute_uri()` began composing `http://`, the OAuth
  `redirect_uri` stopped matching what Google and GitHub had registered, and
  both providers refused the handshake.

Neither new default is wrong — both are the safer ones. The defect is that the
service let a library default answer a question about whose users these are.
The scaffold now answers it, by role:

* **`config/settings/base.py` always states `JWT_CREATE_USERS_FROM_TOKEN`**,
  with `False` for a service that installs `stapel_auth` (it ISSUES the tokens
  and owns the user table — an unknown subject in a token it signed itself is
  stale and must not forge an account) and `True` for every other service (it
  CONSUMES a neighbouring auth service's tokens — the local row is a shadow
  copy). Both roles are spelled out in the comment above the line, in every
  preset: monolith, microservices and minimal, through `stapel-create-project`
  and through `stapel-new-service` alike.
* **The deploy env states the proxy.** `.env`/`.env.example` carry
  `STAPEL_TRUST_PROXY_SSL_HEADER=True` with the precondition written out — only
  where the single way in is a proxy that OVERWRITES the header, which this
  stack satisfies by construction (nginx is the only container publishing a
  port and sets `X-Forwarded-Proto $scheme` on every location). The prod
  settings template no longer claims the library "already trusts" the header;
  since core 0.24 that sentence was false.
* **`OAUTH_CALLBACK_BASE_URL`** is emitted next to the OAuth credentials in the
  monolith env and read in `base.py`. A contract with a third party is
  configuration; deriving it from a request header is what broke it.

### New rule: CFG007 — identity trust must be stated, not inherited

`stapel-config-lint` (and therefore `stapel-verify`, which composes it) fails a
service that mounts Stapel JWT authentication and never states
`JWT_CREATE_USERS_FROM_TOKEN`. A mount is a star-import of
`stapel_core.django.settings` (which brings
`DEFAULT_AUTHENTICATION_CLASSES = JWTCookieAuthentication` with it — the shape
the incident actually had), `stapel_auth` in `INSTALLED_APPS`, or a
`stapel_core.django.jwt` authentication class named directly. The answer counts
from any tier of the settings package, and the message carries both roles so
the value can be picked from the error alone.

CFG007 answers **per settings package**, not per checkout: in a microservices
repo `stapel-verify .` runs at the root, and a service that stated the setting
must not answer for the neighbour that did not — that false negative is the
incident's own topology. And "installed" means a member of `INSTALLED_APPS`,
"wired" means a string inside `REST_FRAMEWORK`; a `LOGGING` block naming the
`stapel_auth` logger mounts nothing. Both were caught in review before the
first release of the rule and are pinned by tests.

The microservices `.env.example` also carries `OAUTH_CALLBACK_BASE_URL`, since
the generated `svc-auth` reads it; the emitted AGENTS/pre-commit/library texts
now say `CFG000-007`.

Unlike every other rule here, a blanket `# noqa` does NOT suppress it — a bare
one sits on the star-import of every real Django settings module (for F403),
and honouring it would switch the rule off exactly where it belongs. Only an
explicit `# noqa: CFG007` counts.

This exists as a fleet rule and not a per-product script on purpose: the same
check was written by hand in one product's `verify_boot_contract.sh` while
every other repo in the fleet stayed silent.

### Also stated: cookie transport, in the tiers that never said it

Found by sweeping the templates for the same class. `SESSION_COOKIE_SECURE`,
`CSRF_COOKIE_SECURE` and `JWT_COOKIE_SECURE` were stated only in the prod tier;
dev/local/test inherited them, and the newer stapel-core defaults them to
`True`. A Secure cookie on a plain-HTTP non-localhost origin — `dev.<slug>.local`,
which the dev tier itself adds to `ALLOWED_HOSTS`, a LAN IP, an http staging
host — is silently dropped by the browser: the admin login form 403s on a CSRF
cookie that was never stored, and a successful JWT login is followed by an
anonymous next request. All three are now `False` in the dev tier and in the
minimal preset's non-prod branch, explicitly.

## [0.42.0] — 2026-08-21

### `stapel-index-lint` — the gate against "indexed silently, read by nothing"

The legacy marketplace's search died in one boring way: fields were written
into the index and read by no query. `features_search` was built on every
publish and never queried. `description_en` was populated and never searched.
`geohash` was stored and never used for proximity. Nothing failed, and nothing
was noticed for years — because writing a field and reading a field are
different files, and nothing connected them.

`stapel-search` answers that with its index contract as DATA (`docs/index.json`:
one row per indexed field, carrying its source, the named query capabilities
that READ it, and the pytest node id that proves the round trip). This release
adds the static half of enforcing it, fleet-wide.

* **`stapel_tools/index_lint.py`** — five rules, the `surface_lint` idiom
  (rule codes, `--json`, `--strict`, exit 1):
  * **IDX001** (error) a field on an index model that `docs/index.json` does
    not account for. The map is explicit, so adding a column forces a
    decision — indexed value with a read path and a test, or bookkeeping that
    says so. "I'll wire the query later" stops being expressible.
  * **IDX002** (error) a query read path some shipped backend does not answer.
    Each backend declares `READ_PATH_IMPL = {read_path: symbol}`; the rule
    checks the promise is registered AND that the named symbol exists in that
    module. A backend may answer `capability:<name>` instead when its engine
    resolves the question natively (Meilisearch has no geohash column) — a
    declared, reviewable difference rather than a silent one. Modules setting
    `IS_STUB = True` are skipped: a rule that forces a stub to grow a fake
    implementation manufactures the defect it audits.
  * **IDX003** (error) a declared proving test that does not resolve.
  * **IDX004** (warning) a document field pulled from the source that lands in
    no index field — dead haulage. A warning, because a mapper legitimately
    reads more than it indexes.
  * **IDX005** (warning) a field `kind` outside the closed vocabulary.
* **Composed into `stapel-verify`**, so every generated project's pre-commit
  picks it up on the next stapel-tools upgrade with nothing to regenerate —
  and it is **silent by design** in a project that ships no `docs/index.json`.
* Waivers are explicit and named, never silence (the
  `adoption_checks.py:26-45` canon): `# stapel: index-waived <field> — <reason>`.
* Level follows the reader's power to act (`adoption_checks.py:53-66`): errors
  in the library that owns the index, warnings in a consuming project that
  merely installed somebody else's backend.

The boundary is SUR004's, restated because it is the same one: these rules
prove **the promise was not dropped on the floor**, not that the branch is
right. Only the round-trip suite does that.

## [0.41.2] — 2026-08-16

### Fixed — the live-circle gate stops reading a credential out of the log

`e2e_live_circle.py` got its one-time code by regex over the captured log
stream, because the mock OTP provider used to print it. stapel-auth stopped
printing it — in mock mode the code is `MOCK_OTP_CODE` by construction, so the
log line was spending a live credential to say what the setting already says.
The gate went red on a library that had just become more careful, which is the
wrong way round.

It now reads `auth_settings.MOCK_OTP_CODE` directly. The log is still captured
and still asserted on, for the thing it can actually attest: that the mock
branch is the branch that ran. Without that check a deployment with mock mode
off would sail past the gate on a code matching nothing.

### Note

0.41.1 was tagged but never published — its `e2e-generated-project` job failed
on the above. Its contents (the boot-smoke tier fixes, and the scaffold floor
moving to stapel-core >=0.29.0) ship here.

## [0.41.0] — 2026-08-16

### A library that requires a setting says so, and the scaffold reads it

`stapel_gdpr` raises the boot-fatal `gdpr.E001` when
`STAPEL_GDPR["DATA_OWNERS"]` is empty, and **both example apps in this
workspace were dead on arrival**: the scaffold installed the app and emitted
the setting nowhere. It could not have fixed that even if it had tried —
`validate_module_config` hard-rejected unknown keys, and `DATA_OWNERS` is not
an axis, so a caller supplying it was refused.

`capabilities.json` grows a **`required_settings`** section: one entry per
setting installing the module makes mandatory, with enough shape (`kind`,
`example`) for a generator to emit a correct placeholder and enough prose
(`why`, `unset_check`) for a human to know what to put there.

*Why there and not `CONFIG.MD`*: CONFIG.MD is env/vault scoped — its rows carry
a `Source` column, `get_config(key)` routes reads through it, and `config-lint`
(CFG001–CFG003) checks env keys against a project's reads. `DATA_OWNERS` is not
an env key: it is a container-shaped key in a Django settings namespace, and
stapel-core now *refuses* to read such a key from the environment at all.
Declaring it in CONFIG.MD would declare it env-sourced, which is false by
construction. `capabilities.json` is already the contract for the
`STAPEL_<MOD>` namespace — `axes` are keys in exactly that dict, and
`_module_config` already reads the file to accept or reject caller-supplied
keys — so the declaration and its reader land in one place. It is also already
emitted and drift-gated by `make contract`.

`create_project` now refuses to generate a project whose selected libraries
declare required settings the caller supplied no value for. The refusal names
each key, the check it prevents, and hands back the paste-ready
`STAPEL_<MOD> = {...}` block. It lands **before any file is written**, and it
runs with no `module_config` at all — "supplied nothing" is the failing case.
Declarations resolve from a workspace sibling *or* the installed distribution,
so the gate is not silently skipped in a plain `pip install` checkout.

### `stapel_tools.shell_python_lint` — SH001/SH002

`iron-auth/bootstrap.sh` ran a heredoc beginning `from common.django.openid
import ...`, a module deleted at the stapel migration. It failed on every boot
for months and nobody knew, because the script did not stop on error.

Full import resolution is not statically decidable — `sys.path` is assembled at
runtime and third-party distributions live in an environment no static tool can
see. The shape the incident actually had *is* decidable, and it is the common
one:

- **SH001** — an embedded payload (`python -c`, a heredoc, `manage.py shell
  <<EOF`) imports `a.b.c` where `a` is a package **in this repository** and
  `a.b.c` is not. Third-party and stdlib imports are not examined at all, so
  the rule has no false-positive story.
- **SH002** — an embedded payload whose exit status nothing reads. Accepts
  either discipline: a `set -e` flag, the per-step `require`/`optional` verbs
  the generated boot contract uses, or an explicit `|| ...` guard.

### Fixed — three things the release itself was carrying

- `stapel-shell-python-lint` is registered as a console script. The module's
  own `argparse` already announced itself under that name, but nothing put it
  on `PATH`, so the lint shipped uninvocable.
- The library scaffold emitted `stapel-core>=0.3.0,<0.4` into every generated
  `pyproject.toml` — a floor so far behind that a freshly scaffolded library
  resolved a core predating the whole mandate/scope surface. Now
  `>=0.27.0,<1.0`. The same defect class the gate this release adds exists to
  catch.
- `test_emit_errors_writes_valid_registry` asserted the exact key set of an
  error entry, so stapel-core 0.26.0 adding `owner` turned the CI red. It now
  requires the five and tolerates `owner`, because this tool reads artifacts
  from cores on both sides of that change.

### Added — a boot contract that cannot lie

The generated `bootstrap.sh` used a blanket `set -e`. Both shapes a
hand-written bootstrap ends up in are wrong: without `set -e` a failed
`migrate` is stepped over and the server starts on a schema its code does not
match (healthy container, 500s on every affected endpoint); with `set -e` a
failed `collectstatic` takes the whole API down over one asset, someone
deletes the flag, and the first shape is back.

So the scaffold now emits `scripts/bootstrap_lib.sh`, a POSIX-sh step runner
with two verbs — `require` (failure makes the service *wrong*) and `optional`
(failure makes it *ugly*) — and a closer, `bootstrap_done`, that asserts the
END STATE with `migrate --check` instead of trusting the step statuses.
Statuses get lost to a pipe, a subshell or a later edit; "bootstrap succeeded
on an unmigrated database" is no longer expressible. The classification
travels with the generator: migrations are `require`, collectstatic and the
superuser are `optional`, each with the reason on the line. Every service
Dockerfile bakes the runner in.

### Added — a deploy gate that checks the result, not the intention

`deploy/check-env.sh` and `deploy/preflight.sh` both run before `up`, so they
verify an intention. `deploy/verify-stand-state.sh` runs after and verifies
the result: healthchecks settled, nothing restarting, nothing unhealthy,
nothing dead non-zero, and no service running behind its own migrations
(`migrate --check` inside each container — the code that is actually running).
Restarts are a DELTA against a baseline the same script writes before `up`
(`--baseline`, called from `deploy.sh`); a lifetime restart counter reported
as "since this deploy" is a claim nothing measured, and without a baseline the
check says NOT ARMED rather than blaming anyone.

`deploy/smoke-services.sh` probes every declared service and makes the result
the verdict — no `set -e` to end the loop on the first unreachable service,
a probed-count assertion so a short run cannot pass as a clean one, and a
required `"schema"` key in the health body so a check whose subject is absent
cannot read as healthy.

### Added — monitoring that can say "wrong", not only "absent"

`service-configs/grafana/provisioning/alerting/rules.yaml`,
`service-configs/prometheus/prometheus.yml`, a pinned Prometheus datasource
and an opt-in `docker-compose.monitoring.yml` that reads them.

Five rules, not two. Container Down and Service Down stay exactly as they are:
an OOM kill does not crash-loop, and reachability is the only thing that
catches it. Container Restarting is keyed on `changes(container_start_time_
seconds)` — monotonic while the fault lasts, so a container coming back every
minute produces one alert instead of hundreds of threshold crossings. Schema
Behind Code reads `stapel_schema_at_head`, emitted only when the state was
determined, and Schema Probe Cannot Answer reads `stapel_schema_probe_ok`,
emitted always: two facts, two sentences, and the second never borrows the
first's words. Containers are selected by compose label rather than by a name
list, which cannot be silent about a service nobody added to it.

`noDataState` is `NoData` on every rule but one. A dead exporter must not turn
the board green (`OK`), and must not fire a rule's own summary for something
it never measured (`Alerting`); Grafana's `NoData` raises its own
`DatasourceNoData` alert. The exception is Schema Behind Code, whose series is
deliberately absent when the schema state is undetermined — there absence is
designed, and the gap is closed by the probe-blind rule.

### Added — the meta-gate

`scripts/verify_boot_contract.sh`, emitted into every containerised project
and wired as a pre-commit hook, checks each service in `services.conf`: the
runner is sourced, no blanket `set -e`, migrations are `require`, no
`manage.py` step escapes the verbs, the Dockerfile bakes the runner in, the
compose command chains `bootstrap.sh` with `&&`, and the schema probe is
present, unmodified and registered. `tests/test_boot_contract.py` runs it
against a generated project and watches every check go red.

### Added — a schema-drift probe on `/api/health/` and `/api/metrics/`

`config/schema_health.py` per service, byte-identical to
`scripts/service_schema_health.py`. Three states, not two: a bool would turn
"I could not reach the database" into "the schema is behind", so every service
would report drift during a database restart. `stapel_schema_at_head` is
emitted only when the state was determined; `stapel_schema_probe_ok` always.
Registered non-critical: a 503 on drift would pull every backend out of
rotation during a rolling migration.

## [0.40.0] — 2026-08-14

### Added — `# stapel: cutover-phase`, the second legitimate shape of a destructive migration

MIG001 knew one way for a destructive operation to be legitimate: it shipped
one release after the code stopped using the target (`# stapel:
contract-phase`). There is a second, and stapel-workspaces has one — a
deletion-driven cutover, where the same migration carries the rows out (a
`RunPython` replaying `WorkspaceAuditEvent` through the event-store facade)
and then drops the table. Code stops using the target and the target dies in
one release, so `contract-phase` on that file would assert something false;
splitting it across two releases would be ceremony around a table that has no
readers left either way.

`# stapel: cutover-phase` says what actually happens, and it has to earn
itself: the linter requires a data-carrying `RunPython` — forward code that is
not `RunPython.noop` — positioned **before** the destructive operation in the
same `operations` list. Copy first, drop second; a file that only destroys is
still MIG001 no matter what comment it carries, and so is a destructive
operation that runs before the data path. `RunSQL` is not accepted as the data
path: from the AST a copying `INSERT…SELECT` and a destructive DDL string look
identical.

What stays the author's assertion, stated plainly rather than implied: that
the `RunPython` carries *this* target's rows (the callable's body is out of
AST reach), and that the deployment is stop-the-world — old and new code never
running against the same schema at once (`docker compose up -d`, which is what
this fleet does). Under a rolling or blue/green deploy the shape is unsafe:
the old process keeps writing to a table already drained and dropped. There,
`contract-phase` and two releases remain the only correct answer. The marker
is not a synonym for `contract-phase` and not a general licence to destroy.

### Added — MIG005: both phase markers on one file is a contradiction, not a stronger claim

`contract-phase` says the code stopped using the target one release ago;
`cutover-phase` says it stops in this one. An author who writes both does not
know which claim they are making, so neither licenses anything: MIG005 reports
the contradiction and MIG001 still reports the destructive operations.

## [0.39.1] — 2026-08-11

### Fixed — `stapel-po-prune --json` emitted a human sentence after the document

The dry-run summary ("N entries would be removed") was printed to stdout after
the JSON payload, so `json.loads` on the output raised `Extra data` — found by
using the flag for exactly what it is for. Machine output is now the only thing
on stdout; the sentence goes to stderr, where it is still visible to a person
running the command by hand.

## [0.39.0] — 2026-08-11

### Added — the canonical i18n command is ours now, and it cannot silently un-translate

`makemessages` is the first thing anybody reaches for the moment they add a
translatable string, and run bare over a product tree it will demote every
entry whose source it could not find. Demotion has two forms and gettext skips
both: **obsolete** (`#~`, parked at the end of the file, at least visible) and
**fuzzy** (left *among the live entries*, still carrying its translation, still
looking translated, and dropped from the `.mo` all the same). Fuzzy is produced
by changes as small as a format-flag flip — `python-format` to
`python-brace-format`, with no edit to the msgid at all.

Measured on meettoday's `backend/`: a bare run takes 100 live msgids to 64 per
locale, 40 obsolete and 5 fuzzy, and the one string that flips to fuzzy is the
passcode subject — after which `gettext("Your {company_name} verification code:
{code}")` under `ru` returns the *library's* default instead of the product's
branded one. Two tests caught that; the other 40 demotions per locale nobody
asserts anywhere, so the suite would have gone green on a product that had
quietly reverted to its source language.

Three commands, one rule.

**`stapel-po-lint`** — the gate. `PO001` fuzzy and `PO002` obsolete are errors
(both are entries gettext will skip); `PO003` untranslated and `PO004` unowned
are warnings. `--max-fuzzy N` / `--max-obsolete N` let a known count stand
while a sweep runs, so the gate still fails the moment the count *rises*. It is
composed into **`stapel-verify`**, which every generated project's pre-commit
already runs — so a project picks the gate up on its next stapel-tools upgrade
with nothing to regenerate, and stays silent in a project that ships no
`locale/` at all.

`PO004` states the general rule as a rule: **a catalogue is a projection of its
own sources; it is never a place to park somebody else's strings.** A library's
strings live in the library's catalogue and ship inside its wheel, where
Django's app-locale discovery merges them at load; a product translates its own
templates and its own code. The check applies only to catalogues that *are*
projections, and the discriminator is mechanical: at least one `#:` reference
resolving to a real file in the tree. A hand-authored library catalogue whose
`#:` slot holds translation keys instead (`#: notification.otp_code.subject` —
how this fleet's own catalogues are written) resolves nothing and is never
judged on ownership. `PO001`/`PO002` apply to every catalogue regardless:
gettext skips fuzzy and obsolete whoever wrote the file.

**`stapel-makemessages`** — the wrapper, so the command people reach for is
ours. It runs the extraction with the ignores Django does not apply itself,
then runs the gate on the result, and **restores the catalogues byte-for-byte
if anything was demoted**. A run that would silently un-translate strings
leaves no trace in the working tree; it leaves a report. `--accept-losses` is
the deliberate escape hatch for when strings really are being retired.

**`stapel-po-prune`** — the product-side fixer, dry-run by default. It asks
`makemessages` (run inside a scratch copy, so the real catalogues are never
touched) what the tree actually contains, and sorts every entry into `sourced`,
`shadow` (the extraction finds it, but an installed package owns the same
msgid — it survives only because something quotes the literal), `foreign` (the
extraction does not find it and a library owns it) and `dead` (nobody owns it).
Only `dead` is removed. `foreign` and `shadow` are reported *with the override
rewritten into the owning library's documented seam* — for stapel-notifications
that is `STAPEL_NOTIFICATIONS["TEXT"]`, keyed by translation key, which the
tool reads out of the library's own catalogue and key registry rather than
asking anybody to type it. Deleting them instead would hand the string back to
the library default, silently.

Asking the extractor rather than grepping the sources is the difference between
right and plausible: a `{% blocktranslate %}` msgid is not a literal anybody
typed (`{{ name }}` extracts as `%(name)s`), so a source scan calls live
entries dead. The scan survives as `--mode heuristic` for trees where Django
cannot be run, it errs toward keeping entries, and the report always names the
mode it used. `--mode extract` refuses to degrade quietly.

The rewrite deletes whole entry blocks out of the raw text, so every surviving
byte is untouched and the result reads as a plain deletion diff. The dry run
**proves** idempotence rather than claiming it: it applies to a scratch copy,
classifies again, and reports what a second apply would remove.

`make messages` / `make messages-check` are wired into the scaffold's Makefiles
(minimal preset and per-service) so a generated project is born reaching for
the wrapper instead of the bare tool.

## [0.38.0] — 2026-08-10

### Fixed — a template tag's options are grammar, not context variables

`templates.json` is derived by walking each template with Django's own lexer,
and the scanner modelled a tag's arguments as `key=expr` pairs plus the legacy
`expr as name`, with a single special case (`only`, for `{% include %}`).
Django's tags do not have that grammar. `{% blocktranslate trimmed %}` fell
through to "then it must be an expression", so the emitted contract declared a
**required context variable named `trimmed`** — a word Django binds nothing
for and no host can supply. `asvar`, `context` and `count` were misread the
same way. On a colleague's email-template branch four test failures were this,
not his templates; the product's workaround was to subtract `trimmed` from the
gate's expected set (with a guard test proving the subtraction had not neutered
the gate) and let the other three keep failing loudly rather than widen the
subtraction. Both can go.

Each modelled tag now carries **its own grammar table**, read off Django's tag
compilers rather than off the templates the fleet happens to ship —
`do_translate` / `do_block_translate` in `django/templatetags/i18n.py`,
`do_include` in `django/template/loader_tags.py`, `do_with` / `do_for` / `now`
in `django/template/defaulttags.py`. An option is one of four things:

* a bare **flag** — `trimmed`, `only`, `noop`, `reversed`: reads nothing, binds
  nothing;
* a **bind** — `asvar <name>`, `as <name>`: a local, readable after the tag,
  never something the host is asked to pass in;
* an **expression** — `context <expr>`: a real context read, including the
  variable form `{% blocktranslate context ctx %}`;
* a **kwargs run** — `with a=b c=d`, `count n=expr`, consumed exactly the way
  `django.template.base.token_kwargs` consumes it, legacy
  `expr as name and expr as name` included, ending at the first bit that does
  not fit the form so a following option word reaches the option walker.

Two more misreads of the same shape ride along: tag contents are now split the
way Django's own `Token` splits them (`context "a greeting"` is one argument,
not two), and `{% translate "x" as var %}` / `{% now "Y" as year %}` bind their
result as a local instead of demanding it from the host.

And the direction this fix must not take: an option word a tag's grammar does
not know is **refused, not guessed** — recorded like an unknown tag, which is
an abort under `strict`. Django raises `TemplateSyntaxError` on exactly those
words; a denylist of known-bad option names would have reproduced the original
defect one option later.

The gate stays **fail-closed**, which is the property a parser fix can quietly
destroy: a variable no provenance declares still aborts emission in a letter
written entirely in these option forms
(`test_a_missing_variable_still_aborts_a_template_full_of_options`), and that
test was checked against a deliberately blinded scanner — it goes red the
moment a block's reads start counting as locals. Every option form in the
table is also compiled by Django's own parser in the same test run, so the
grammar under test is Django's and not ours.

## [0.37.0] — 2026-08-10

### Fixed — the generated image now carries a decoder for every format the settings promise

stapel-cdn 0.10 removed Pillow and made libvips the single decoder on the
image path: validation and processing ask the same engine, so "declared
allowed" and "actually decodable" can no longer drift apart *in code*. What
they can still drift apart in is the **image**, and that is what
`DOCKERFILE_CDN` emits.

Measured in the built container (2026-08-10, arm64, libvips 8.16.1), one real
file per format, full pixel pass — not read off a manpage:

| ext | loader | before | after |
|---|---|---|---|
| .jpg/.jpeg | jpegload | OK | OK |
| .png | pngload | OK | OK |
| .gif | gifload | OK | OK |
| .webp | webpload | OK | OK |
| .bmp | **magickload** | OK | OK |
| .heic/.heif | **heifload** | OK | OK |
| .avif (not in the stock allowlist) | heifload | OK | OK |

So nothing was missing — and that is the finding, not a clean bill of health.
Every one of those loaders arrived as somebody else's transitive `Depends`:
`libvips42t64` → `libheif1` → `libheif-plugin-libde265`, and `.bmp` only
because libvips's ImageMagick module happens to be compiled in (libvips has no
native BMP reader at all). Nothing in the Dockerfile said we needed any of it.
A promise held by accident is held until the day it isn't, and the day it
isn't, an iPhone photo is told it is an invalid file.

The runtime stage now **names** them:

```
libvips42t64 libheif-plugin-libde265 libheif-plugin-dav1d
```

Naming them *is* the build-time gate — apt fails the build the day Debian
renames or drops one, instead of the image quietly losing a format. Whether
the assembled image honours a *host's own* `ALLOWED_IMAGE_EXTENSIONS` stays
`stapel_cdn.checks.E004`, a system-check Error at boot; verified both ways in
the container — silent for the stock allowlist, and still firing (2 findings,
`.heic`/`.heif`) once `vips-heif.so` is removed, so it has not been defeated.

Two changes ride along, for the same reason:

* **the runtime stage installs `libvips42t64`, not `libvips-dev`.** Headers,
  and the ~40 `-dev` packages behind them, are the `vips-builder` stage's
  business. Same eight formats decodable: **662 MB → 331 MB.**
* **both stages pin `python:3.12-slim-trixie`.** The floating tag moved
  bookworm → trixie under the fleet. Both releases happen to decode all eight
  (measured), but a Debian release is exactly the event that changes a decoder
  set quietly, and version-named packages need a named suite to be named
  against.

No format is recommended for removal from the stock default: `.bmp` costs
nothing extra (ImageMagick is a hard dependency of libvips itself, dropping
`.bmp` would not remove a byte), and HEIC is the format an iPhone actually
uploads.

## [0.36.0] — 2026-08-10

### Added — a generated project is born knowing when a template variable went missing

The scaffold now writes `config/settings/test.py` (`from .local import *` plus
stapel-core 0.21's `strict_template_variables(TEMPLATES)`) and points
`pytest.ini` at it. An unresolved template variable renders as a visible marker
under test instead of Django's default empty string, and the generated comment
says how to assert on it and — the part that matters — that it is the net and
not the closure: the closure for a template you override from a library is that
library's `docs/templates.json`, read with `template_contract.declared_for`.

The mechanism existed one product at a time before this. That is how the next
product starts where the last one did.

## [0.35.0] — 2026-08-10

### Added — `stapel_tools.template_contract`: the sixth artifact, for the one surface with no contract

Django templates shipped in a library are an extension surface — a host drops
a file of the same name into a directory that resolves first and the letter is
theirs. It was the only such surface in the fleet with nothing declared about
it: `capabilities.json`, `errors.json`, `flows.json`, `schema.json` and
`llms.txt` between them name not one template path and not one context
variable. So a host obtained the contract by reading the library's service
code, and the library could break that host twice over with every test on both
sides staying green:

* rename a context variable → Django's `string_if_invalid = ''` renders the
  hole as an empty string, so the mail ships with a blank where the OTP code
  was: 200 OK, no exception, nobody can log in;
* rename a template file → the host's override shadows nothing and the
  LIBRARY's letter goes out under the host's brand, while the host's guard
  ("this resolves from our folder, not site-packages") stays GREEN, because it
  asserts the name the host itself chose and that file still exists. The guard
  matches by name while the override is dead.

`build_document()` emits `docs/templates.json` — routing key → template path →
the whole `{% extends %}`/`{% include %}` chain → the context variables the
library passes, grouped by provenance. Same discipline as the other emitters:
deterministic render, `--check` drift gate, loud failure instead of a partial
artifact.

Two derivation halves, neither of them retyped by hand:

* `scan_call_site()` reads the Python AST of the module that renders. Every
  literal-key write into the context dict is a declared variable; a write
  reachable only under an `if` is `conditional` and carries the guard's source
  text; a write under a computed key is reported as `dynamic_keys` rather than
  guessed at.
* `scan_source()` / `resolve_chain()` read the templates with **Django's own
  lexer and filter-expression parser**, not a regex — so a filter argument
  (`{{ host|default:name }}` reads both) is a read, a loop variable is not a
  context variable, and an `{% include %}` sitting inside an `{% if %}`
  contributes optional rather than required reads.

It is loud where it cannot be sure: an unmodelled template tag, a template a
route names but nobody ships, a render call site whose wiring no longer matches
the declared one, or a template that reads a variable no provenance declares —
each aborts emission naming the thing. And it states its own edges in the
artifact's `limits` rather than implying completeness.

`load_contract()` / `declared_for()` are the consumer half, for a HOST project's
gate: point them at the installed dependency and assert that every template you
override still exists upstream at that exact path and that every variable your
template reads is still declared. `declared_for()` raises on an unknown path
instead of returning an empty set — an empty set passes a subset check, which
is precisely how the dead-override failure hides.

## [0.34.0] — 2026-08-09

### Fixed — the scaffold's `@stapel/core` pin builds again

`@stapel/notifications-react` 0.6.1 imports `useErrorText`, which `@stapel/core`
only exports from 0.11.0; the scaffold still pinned 0.8.1. npm installs that
combination happily — the peer range is `>=0.3.0 <1.0.0` — and the generated
frontend then fails at BUILD time on a missing export, which is what the
`e2e-generated-project` job has been red on.

### Added — SWAP004: a vendor SDK belongs to the library that owns the seam

A product carried its own copy of a LiveKit provider next to `stapel-video`'s.
It was not a bad copy — it was *ahead* of the library on two capabilities, and
that is the whole mechanism. A fork of a provider layer never starts as a fork;
it starts as one call the library did not have yet, added where the engineer
was standing. Every capability added there is one no other consumer ever gets,
and the day the library fixes something real (a rename that reaches a call
already in progress) the product with the fork cannot receive the fix at all.

`SWAP004` (error) flags a direct import of a vendor SDK a fleet library owns
the integration for, from anywhere outside that library — today `livekit`
outside `stapel_video`, from a table (`_VENDOR_SDK_OWNERS`) that gains a row
the day a library ships the capability for the fleet, not before. It is not a
dependency ban: depend on the SDK, run it in a worker. What you may not do is
*import* it, because that is the one act that puts a provider call in product
code. The fix is always the same — add the capability to the library's provider
contract and call it through the seam.

Lazy imports inside functions are flagged too (that is where a fork actually
grows); relative imports, look-alike package names, `tests/`, and
`# noqa: SWAP004` are not. Composed into `stapel-verify` with the rest of the
family.

## [0.33.0] — 2026-08-09

### Added — `stapel-readme`: README.md becomes an assembled artifact, not a hand-written monolith

Every README in the fleet was hand-written, so every README rotted, and it
rotted in the part a reader trusts most: the numbers. A hand-typed version, a
hand-copied badge row, a hand-curated list of links are all restatements of
artifacts that already exist in machine form — and restating them by hand is a
promise to restate them again on every release, which nobody keeps. The fleet
has three tracker items about exactly this failure class.

The split (#257): the **static** half — what the library is, why it exists, how
to think about it — lives in `docs/readme.md`, written by a human. The
**generated** half is assembled by `stapel-readme` from `pyproject.toml` and
`docs/{capabilities,schema,errors,flows}.json`: title, badge row, install line,
an "at a glance" facts table (version, Python/Django floors, HTTP operations,
config axes, usage surface, extension points, error codes, flows, fleet
dependencies), documentation links in every language the module ships,
cross-links to READMEs in other languages, and the licence footer.

Three properties, all of them the point:

- **A badge that cannot be true is not emitted.** Every badge of
  `docs/pending/badge-canon.md` §1.1 has its precondition checked in the
  checkout — CI iff `ci.yml` exists, coverage iff `codecov.yml` exists *and*
  the workflow uploads, `python` iff there are `major.minor` classifiers,
  `license` iff there is a `LICENSE`, `llms.txt` iff the file exists.
  Publication is the one fact a checkout cannot prove, so it is declared
  (`[tool.stapel.readme] pypi`, default true) and `--verify` checks the
  declaration against PyPI over the network — deliberately outside the
  hermetic drift gate. An unpublished module gets a `status-unreleased` badge
  and a `pip install git+…` line (canon §4.1) instead of a badge rendering
  "package or version not found".
- **Contradictory inputs stop the render.** A `capabilities.json` version that
  disagrees with `pyproject.toml` fails the emission with both numbers and
  writes nothing. That is #226 verbatim; a generator that quietly preferred the
  fresher input would leave the other free to rot forever.
- **Doc links are absolute.** README.md is also the PyPI long description,
  where a relative link is a 404 — as every hand-written README in the fleet
  currently demonstrates.

`--check` is the drift gate (byte-for-byte, same contract as
`stapel-llms-txt`); the render is deterministic; `docs/readme.<lang>.md`
produces `README.<lang>.md` with a language switch between them.

Proven end to end on `stapel-auth` (112 operations, 28 axes, 127 error codes,
4 flows, 3 error-doc languages) and `stapel-mailtrap` (capabilities only, no
schema/flows/errors) — the two extremes. Rollout plan for the rest:
`docs/pending/readme-as-artifact.md` in the workspace.

### Added — `stapel-storefront`: the public page, generated from the catalogue

We publish 26 packages and ship an `llms.txt` everywhere so agents can discover
us, while a human who follows a link sees an organisation page of bare
repository names (#259). `stapel-storefront` assembles that page — a library
table with badges, the roll-up totals, and the quickstart — from the catalogue
aggregate (`stapel_tools.catalog`, drift-gated per #184) joined with each
module's `pyproject.toml` for the badge preconditions.

No version number is typed anywhere on the page: the version and download cells
are live shields/pepy badges, so a page committed today still tells the truth
about a release made tomorrow. Badges come from `stapel_tools.readme`
unchanged — one canon, one implementation, used by both the module READMEs and
the storefront.

`--format md` writes `index.md` (the shape GitHub renders as an organisation
profile page — no hosting, no DNS, no build); `--format html` writes a
self-contained `index.html`; `--check` is the drift gate.

## [0.32.0] — 2026-08-09

### Added — SWAP003: a dotted path may point at your own things, or at what config chose. Not at somebody else's package.

`stapel-workspaces 0.19.0` asked Django's app registry whether
`stapel_profiles` ran in this process, and then resolved
`stapel_profiles.validators.validate_display_name` and a model accessor by
string. It worked in a monolith and answered a permanent 503 in a split
deployment, because **a symbol resolution has no remote form**. An audit found
it was the only cross-module symbol resolution in the fleet — the anomaly, not
the pattern; everything else goes through comm Functions, which are
topology-independent by construction.

SWAP003 flags a **hardcoded dotted-path literal whose top-level package is not
ours**, handed at runtime to `import_string`, `importlib.import_module`,
`apps.get_model` / `apps.is_installed`, `find_spec`, or `getattr` on an
imported module object. That is a hidden import across a module boundary with
none of an import's honesty: no dependency declaration, no version constraint,
no failure until runtime.

The line it draws is the whole design, and it is decided at the call site with
no index and no configuration: **where did the value come from?** A path that
arrives from configuration — `STAPEL_RECORDINGS["STORAGE"]`, `NORMALIZER`,
`PIPELINE_RESOLVER`, the GDPR provider registry, merge-registries keyed by
kind, every `get_model(KEY, default=...)` swap seam SWAP001 already guards —
never hands a string constant to the resolver, so the rule never sees it. A
literal naming your own package is likewise silent: "only to your own
overridable entities" is exactly what that means. What is left is the
undeclared reach at a peer, which is what the incident was.

"Ours" is derived, never configured, and there is no allowlist to join: every
top-level package in the tree, every `AppConfig` label, the `pyproject.toml`
name, everything the manifest pins (`[project.dependencies]`, any extra, any
`requirements*.txt` anywhere in the tree), the standard library, and `django`
itself. `stapel-workspaces` declares `stapel-core` and nothing else, so
`stapel_profiles` is foreign to it by its own manifest.

It also folds string constants and exactly ONE level of local helper. That is
not a flourish: the incident wraps the probe and the `import_string` behind
`profiles_in_process(dotted_path)` and puts every literal at the *call* sites.
An earlier draft that read only resolver arguments found a parameter, cleared
the file containing the defect, and reported zero across the fleet — a dead
rule that ships looking healthy. The 24 new tests assert both directions for
every case for the same reason.

Composed into `stapel-verify`, so every consumer gets it on upgrade.

**Measured across the whole fleet — 37 repos plus `ironmemo-backend` and the
meettoday backend — before shipping: 34 raw hits, 3 after triage.** Each
exclusion was paid for by a class of hit that was not a defect: 11 ×
`getattr(mod, "__version__")` on a statically imported optional dependency
(`ironmemo-backend`); 5 × `apps.is_installed("django.contrib.*")`, which asks
whether the host turned admin on — configuration, not topology (`stapel-core`,
`stapel-recordings`); 4 × sibling repos checked into `stapel-studio/.vendor/`,
linted as if they were that project's code; and everything the manifest pins
(`pyvips` behind `stapel-cdn`'s `images` extra, `stapel_core.django.taskstore`
from `stapel-recordings`, `meeteval==0.4.3` from `ironmemo-backend`). Two more
classes need no exclusion because the design already makes them invisible, and
both are pinned by tests: dotted paths inside stapel-tools' own code templates
(generated source text, not resolution) and Django's settings strings
(`AUTH_USER_MODEL`, `MIDDLEWARE`, `DEFAULT_AUTO_FIELD`) — assignments, not
calls.

The 3 that remain are named, not silenced: `stapel-core`'s
`adoption_checks.py:124` (`is_installed("stapel_auth")`) and
`stapel_preflight.py:295` (`is_installed("stapel_workspaces")`), both
diagnostics whose subject genuinely is the deployment topology, and
`stapel-workspaces`' `_codegen_settings.py:56` (`find_spec("stapel_profiles")`),
a test harness assembling a co-mounted monolith. A `checks.py`-shaped exemption
was considered and rejected: a hole defined by file location is a hole people
learn to hide behind, whereas three `# noqa: SWAP003` lines with a reason are
greppable and reviewable. `stapel-workspaces`' four product-code hits were
fixed in that repo while this shipped; the rule fires on the committed defect
and is silent on the fix.

## [0.31.0] — 2026-08-09

### Added — R010/R011: the source is English, and homoglyphs are caught

R010 flags Cyrillic in a comment, a docstring or an identifier. It ignores
plain string literals on purpose: Russian data is legitimate (i18n catalogues,
e-mail bodies, fixtures whose Cyrillic is the thing under test), Russian prose
and names are not. Because data is exempt the rule needs no per-path
allowlist — and a rule with no allowlist is one nobody learns to silence.

R011 flags a word carrying both alphabets. Those are the expensive ones:
`miттudei` reads as Latin, greps as neither, and survives review because the
eye cannot tell the two т apart. Five such words were found across the fleet,
including a section citation `§Р13` with a Cyrillic Р.

Both rules also run on test files, unlike the layer rules — Russian names were
thickest exactly there, and pytest prints those names.

### Fixed — the release pipeline had been red since 0.29.2

A single unused `import pytest` in `tests/test_config_lint_cfg006.py` failed
ruff on every matrix branch, which took CI on main down and blocked publishing.
0.29.2 and 0.30.0 were tagged and never reached PyPI because of it; the last
published version was 0.29.1. Their contents ship here.

## [0.29.1] — 2026-08-06

### Changed — the scaffolded NATS gets headroom for a Function reply

`--max_payload 8388608`. NATS caps a single message at 1 MiB by default, and a
comm Function is request-reply over exactly that. Measured on ironmemo
(2026-08-06): an `llm.complete` reply over a meeting transcript exceeded the
cap, the reply was refused inside the subscription callback, and the caller sat
until its timeout while the work had already been done. stapel-core 0.19.0 makes
that failure visible (`FunctionPayloadTooLarge`); this is the headroom half —
and only headroom: past 8 MiB the answer has to be a reference, not a bigger
message.

## [0.29.0] — 2026-08-05

### Fixed — NGX005: the cache canon cached a MISSING chunk for a year

Measured live on BOTH stands while verifying the cache policy on request:

    curl -I https://app.ironmemo.com/assets/nope-00000000.js
    -> HTTP 404 + Cache-Control: public, max-age=31536000, immutable

The `always` flag on the hashed-asset location makes nginx emit that header on
ERROR responses too. So a 404 for a chunk that is not on disk yet is cached as
`immutable` for a year: the browser will not recheck even on reload. Any deploy
window where index.html is already new and a chunk has not landed leaves
whoever hit it with a permanently broken app until they clear their cache by
hand — the same window the 0.28.0 atomic swap closes, except this one survives
the swap because it lives in the client.

The header on the entry document keeps `always` (no-cache on an error is
harmless); only the long-lived one is dangerous. Without `always` nginx still
adds the header to 2xx/3xx — 304 included, so revalidation is unaffected.
Fixed in `NGINX_CONF` and the per-frontend block, and guarded by the new rule.

### Fixed — two blind spots that made this gate report success about files it never read

* `discover_confs` looked only under `service-configs/nginx*/`. meettoday keeps
  its confs in a plain `nginx/` directory, so this gate had **never** checked
  meettoday: it printed "no nginx conf found" and exited 0. Honest wording,
  zero coverage.
* `serves_from_disk` required the location to declare its own `root`. nginx
  INHERITS `root`, and meettoday's `location /assets/` declares none — so both
  NGX002 and NGX005 skipped the exact block they exist to check.

With both closed, the gate finds the real defect in both products.

## [0.28.1] — 2026-08-05

### Fixed — the generated pnpm image did not build

Verified live by building `ironmemo-frontend`'s generated Dockerfile: `pnpm
install --frozen-lockfile` exits 1 with `ERR_PNPM_IGNORED_BUILDS`. pnpm 10
refuses to run dependencies' lifecycle scripts unless the repo lists them in
`pnpm.onlyBuiltDependencies`, and a Docker build has no way to answer the
interactive `pnpm approve-builds` prompt (ironmemo needs them for `esbuild`
and `@tailwindcss/oxide`). The build stage now sets
`dangerouslyAllowAllBuilds` — those same scripts already run on every
developer's machine (esbuild without its postinstall has no binary and the app
does not build at all), so this REPRODUCES the local situation rather than
widening trust. A repo wanting a narrower answer declares
`onlyBuiltDependencies` and the line stops mattering.

Verified end to end after the fix: image builds, the publish step writes
`/output/<build-id>` and repoints `current`, a second run leaves the previous
build's hashed assets in place, and pruning keeps exactly
`FRONTEND_KEEP_PREVIOUS` old builds beside the live one.

## [0.28.0] — 2026-08-05

### Added — frontend delivery is one mechanism, and a gate watches the seam

Verdict `tasks/fable/frontend-delivery-split-repo.md` (tracker #237).

The §57 canon — a one-shot writer filling a volume, with nginx gated on
`service_completed_successfully` — lived only in the MONOLITH compose template.
The microservice template carried none of it: its nginx mounted only
`./service-configs/nginx`. The canon did not travel, and nothing noticed.

Live consequence on ironmemo: nginx served `root /frontend-react`, a bind onto a
host directory that both `scripts/deploy_stand.sh` and `.gitlab-ci.yml`
explicitly EXCLUDED from rsync. No build ever landed there. For months this read
as "the frontend does not update" and was repeatedly diagnosed as caching.

**`render_frontend_delivery()`** now renders the delivery shape for all three
templates from one function, driven by a `Frontend` record whose `delivery` field
is the configuration axis:

* `build` — compose builds it from a sibling directory (the monolith, unchanged)
* `image` — a dist-carrier image pinned by `${FRONTEND_IMAGE}:${FRONTEND_TAG}`;
  the split-repo answer, and the new default for `--type microservices`
* `host` — a bare bind. Legacy, permitted, and the template says out loud that
  compose has no writer for it rather than looking complete.

`depends_on` is templated into the BASE, not an overlay: several docker compose
versions refuse to override a service that arrived through `include:`, so gating
nginx from the prod overlay would work on the author's machine and fail on the
stand.

**Atomic-ish swap** replaces `rm -rf /output/* && cp -r dist/. /output/`. That
one-liner 404'd the site for the length of the copy AND deleted the previous
build's content-hashed chunks, so every tab open across a deploy broke on its
next fetch. Builds now land in their own directory, `current` is repointed, and
`FRONTEND_KEEP_PREVIOUS` previous builds stay. Honest limit: `ln -sfn` is
unlink+symlink, so the window is sub-millisecond, not zero.

**`stapel-frontend-delivery-lint` (FED001–FED006)** — the gate on the seam
"nginx root ↔ who writes to that path". FED001 resolves every disk-served
frontend root to its mount and demands a provable writer, and separately checks
that a bind source is not `--exclude`d by the deploy script or CI — that second
half is what catches ironmemo. FED002 refuses a mutable image tag outside the
local stack, FED003 an unpinned `FRONTEND_*` variable, FED004 a contract-digest
mismatch, FED005 anything unparseable on the delivery path (error, never a
silent skip — a conservative skip is how ironmemo went unnoticed), FED006 warns
on a bind nobody builds. Composed into `stapel-verify`, so it reaches the whole
fleet through a `stapel-tools` upgrade.

**`stapel-frontend-repo-init`** writes the publishing half into a SEPARATE
frontend repository: dist-carrier Dockerfile (not an nginx image — the project's
own nginx stays the single boundary owning reserved paths, TLS, the proxy table
and the cache canon), the publish script, and a CI job pushing an immutable
`sha-<gitsha>`.

### Fixed

`stapel-new-service` appended `- <svc>` under an nginx `depends_on` that is now a
MAPPING, producing a compose file that would not parse at all (`did not find
expected '-' indicator`). It now detects the existing shape and speaks it.

## [0.27.0] — 2026-08-05

### Changed (BREAKING for meta authors) — every module must answer the surface question

`build_surface()` used to treat a `capabilities.meta.json` with no
`surface_roots` as "no surface section" and emit nothing. That made two
opposite states look identical: a module that genuinely exposes nothing to a
product, and a module whose author simply had not got round to declaring one.
Both produced silence — and silence is what the `surface` section exists to
abolish (an agent reading `llms.txt` cannot tell "nothing here" from "not
described yet", so it writes its own copy of what the module already ships).

A meta now has to say which of the two it is:

- `surface_roots` — the module has a usage surface, and the roots select it
  (unchanged; the LOUD rule still fails emission on a selected symbol without
  an `intent`).
- `no_surface` — a **non-empty sentence** saying WHY the module exposes
  nothing. A bare `true`/`""` is rejected: that is the same silence under a
  new key.

Declaring neither is a hard `SystemExit` naming both options. Declaring both is
a hard `SystemExit` too — they contradict.

When emptiness is declared, `capabilities.json` now carries `"surface": []` —
an **empty list, not an absent key** — so downstream readers see the
declaration rather than inferring it from a missing field.

Four preset modules (`stapel-booking`, `stapel-classified`, `stapel-social`,
`stapel-shop`) were exactly the "no key at all" case and now declare
`no_surface` with a real reason.

## [0.26.0] — 2026-08-02

### Added — `stapel-catalog` emits the fleet's root `llms.txt` index

Badge-canon §3 p.5: an agent that does not yet know which module it needs
should read ONE small file, not `catalog.md` in full or all 26+ modular
`docs/llms.txt` files. `stapel-catalog` now emits `llms.txt` alongside
`catalog.json`/`catalog.md` — one line per module, its `provides` one-liner,
and a link to that module's own `docs/llms.txt` (a GitHub blob URL from the
`STAPEL_LIBS` registry, falling back to a workspace-relative path for a module
the registry doesn't carry).

Same three properties as `stapel-llms-txt`, reused rather than reinvented:
deterministic (sorted by module name), a hard token budget
(`DEFAULT_TOKEN_BUDGET`/`--llms-budget`, `EmitError` rather than truncation),
and LOUD about partial coverage — a module counts as "described" only when its
`docs/llms.txt` actually exists on disk (or in the wheel under
`--from-installed`); the rest are listed by name under "Not yet described",
never silently dropped. `--check` now also drift-gates `llms.txt`.

## [0.25.0] — 2026-08-02

### Added — `stapel-llms-txt`: the module describes itself to an agent, and the description cannot drift

`llms.txt` existed in **none of the 27 Python libraries**. The frontend has had
one per package since `scripts/gen-manifest.mjs` shipped, and it has never gone
stale for a structural reason: the file is *generated from the same artifacts as
the code* and stands under `git diff --exit-code`. Hand-written, it rots on the
first release that moves a symbol, and it rots **silently** — nothing reads it
except a model, and a model cannot tell a stale surface from a current one. The
measured cost of exactly that: the studio index listing 25 modules of 26, with
the auth version eleven releases behind.

`docs/llms.txt` becomes the **fifth per-module contract artifact** next to
`docs/{schema,flows,errors,capabilities}.json`, in the same pipeline: emitted by
`make contract`, gated by `make contract-check`, committed, shipped in the wheel.

The main section is **`surface`**. `axes` answer "what can I switch on" and
`schema` answers "what can I call over HTTP", but the question that cost six
mechanisms their adoption — a permission class, a safety gate, a published
capability field, a nav template, a loader factory, a set of error predicates —
is *"is there already something for X, and what is it called?"*. Only `surface`
answers it, and its `instead_of` line names the outside symbol a product would
otherwise reach for.

Three properties are copied from the frontend generator, not reinvented:

- **Deterministic.** Every list has an explicit sort key (axes by key, surface
  by the closed kind vocabulary then name, operations by tag then operationId,
  errors by code, flows by id); no timestamps, no absolute paths, no
  environment values. Reversing the source document's ordering produces a
  byte-identical file — asserted in the tests, because a drift gate that
  compares bytes cannot tolerate any incidental order.
- **Hard token budget**, 4000 — the frontend's `LLMS_TOKEN_BUDGET` verbatim, so
  both halves of the fleet describe themselves at the same cost. Over budget
  **fails** with a per-section cost breakdown and writes nothing at all. It
  does not drop a section, elide intents or cut the tail: a truncated context
  file reads exactly like a complete one at the point of use, which is how two
  silent truncations already survived review. The message names the trim order
  (`surface → axes → extension_points`) and the deliberate alternative,
  `--budget N` in the module's Makefile. Measured: `stapel-auth` renders at
  ~7261 tokens (operations 2027 · axes 1892 · errors 1542 · surface 1183) and
  is therefore a deliberate decision, not an accident.
- **Loud when there is nothing to say.** A module with no
  `docs/capabilities.json` (`tools`, `vault`, `runner-protocol`, `taskspecs`)
  is an error naming the module. An empty `llms.txt` is worse than a missing
  one: it answers "does the fleet have a mechanism for X?" with a confident no.
  `--skip-missing` makes it an explicit, still-loud no-op for fleet loops.

Token economy is in the rendering, not in what gets dropped: the shared mount
prefix is stated once and each operation line carries only what distinguishes
it (`GET /things/{id}/ — demo_retrieve`), error lines keep code, status,
remediation and interpolation slots while the localized prose stays in
`errors.<lang>.md`, and flows are an index rather than a transcript. Sections
whose source document is absent do not appear at all — `stapel-core`, which has
no OpenAPI surface and no axes, renders as usage surface plus seams at ~1824
tokens.

`--out` renders a checkout the caller must not write to (a foreign repo under a
drift gate); `--stdout` skips the file entirely.

## [0.24.0] — 2026-08-01

### Added — `stapel-surface-lint`: reinvention fails before the merge, not after

`stapel-adoption-lint` catches non-adoption **after the fact** — the module is
pinned, installed and simply not mounted. By then the product has already lived
without the mechanism and the finding arrives as archaeology. These four rules
read the `surface` section shipped in 0.22.0 and fail on the first CI run of the
branch that reinvents one of its entries, so the price of not looking drops from
a production incident to a single iteration. All four are composed into
`stapel-verify`, which every generated project's pre-commit already runs — a
project picks them up on its next upgrade with nothing to regenerate.

Each rule was built against the incident that motivated it and measured against
the whole fleet (26 libraries, the legacy marketplace tree, the studio slice and
two live products — 40 repositories). Numbers below are that measurement.

- **SUR001 duplicate-of-surface** — a class subclassing `BasePermission` under a
  name an installed module already publishes as a `permission_class`. **6
  findings, all in one repository**: the legacy `marketplace-common-python`
  keeps its own `IsNotAnonymousUser`, `IsStaffUser`, `IsSuperUser`,
  `IsServiceRequest`, `ReadOnlyOrStaff` and `ReadOnlyOrSuperUser` beside the six
  `stapel-core` ships. The design's broader form ("any `BasePermission`
  subclass while the index holds a `permission_class`") was rejected on
  measurement: it flags every legitimate domain permission a product must own
  (`IsWorkspaceAdmin`, `IsAdOwner`, `IsReportModerator`), and a rule that reds on
  a product's own domain gets muted wholesale.
- **SUR002 instead_of** — a symbol a module explicitly displaces
  (`IsNotAnonymousUser.instead_of = [rest_framework.permissions.IsAuthenticated]`)
  sits in `permission_classes` while the replacement is used **nowhere in the
  project**. **11 findings across 10 repositories**, one per displaced symbol.
  The per-call-site form the design sketches was measured first and dropped: 13
  findings in a single product and 67 occurrences fleet-wide, nearly all of them
  a deliberate "this endpoint is open to guests". What is never deliberate is a
  repository that has not once heard of the replacement — and `AUTH_ANONYMOUS`
  defaults to **on**, so those guest sessions are authenticated and sail
  straight through `IsAuthenticated`. Both DRF import idioms resolve
  (`IsAuthenticated` and `permissions.IsAuthenticated` — the second is the
  majority spelling, 35 view classes in one product; matching only the first
  would have been an accidental narrowing rather than a decided one).
- **SUR003 imported-but-never-called** — a `gate_function` bound by an import
  and never mentioned again. **0 findings fleet-wide**, which is the honest
  result: every live import of `redaction_gate` / `sanitize_for_rag` /
  `detect_pwned_markers` calls it. Verified by reproducing the incident on the
  real file — `iron-recordings`' `mic_stage.py` is clean with its call and reds
  naming `redaction_gate` with the call removed, which is exactly how the gate
  was lost during the lab port: the import survived, the call did not, and the
  protection became its own appearance. Four legitimate no-call shapes are
  cleared first, all of them observed in the fleet: `__init__.py`, a name in
  `__all__`, an import under `if TYPE_CHECKING:`, and any other reference to the
  bound name (a callback, a registry value, a `functools.partial` — a deferred
  call is a call).
- **SUR004 publisher-without-consumer** — a `capability_field` declaring
  `consumer: frontend` that the `-react` package reads nowhere outside its
  generated OpenAPI types. **2 gaps, reported to the 2 repositories that can fix
  them**: `email_mock` and `phone_mock` occur in `auth-react`'s
  `src/api/generated/schema.ts` and in nothing else, while every sibling field of
  the same two DTOs occurs 14–179 times in hand-written components. Typed,
  published, unread — which is how a dev deployment's screen says "code sent"
  when nothing was sent. Reporting to every repository that can merely *see* the
  gap cost 78 findings to say the same 2 things, so the rule is scoped to the
  publisher and the consumer.

**What these rules do not catch, stated in the docstrings rather than implied:**
prevention *before* the code is written (outside the studio pipeline, code is
written first and checked second — these buy one iteration, not clairvoyance);
a semantic duplicate with no structural signature (SUR001 matches the published
*name*; a reinvention called `IsRealAccount` is invisible to it, and an
AST-similarity heuristic was measured against the fleet's actual renamed
reimplementations — `IsInternalService`/`IsServiceAPIKey` beside
`IsServiceRequest` — and matched none of them while promising noise); predicates,
the genre that leaves no trace at all; a gate passed around instead of called;
and a consumer that reads a field and then ignores it.

**Inert until the contract documents ship.** The index is sourced from the
installed distributions first (`stapel-catalog --from-installed`'s machinery) and
from `--workspace` checkouts second. Today's products pin module versions built
before 0.23.0 taught the scaffold to put `docs/capabilities.json` in the wheel,
so in a product venv the linter emits one honest note and skips — and turns
itself on, per module, as modules republish.

## [0.23.0] — 2026-07-30

### Added — `stapel-catalog --from-installed`: the environment is the source

A committed catalog is a snapshot, and a snapshot without a gate rots
silently. This project proved that on itself twice: the `catalog.json` /
`catalog.md` pair committed at the repo root still described **10** modules
against a fleet of **26**, and the studio's `stack_index.json` still said
`stapel-auth@0.7.5` against `0.18.0` on disk. Both were generated once, by
hand, and never again.

`--from-installed` removes the snapshot from the loop entirely: it sources
the aggregate from the **current environment** — every installed `stapel-*`
distribution that ships `docs/capabilities.json` in its wheel — so the index
becomes a pure function of the lockfile and *cannot* lag the code that will
actually run. Two lookups per distribution: the wheel's own RECORD first,
then a `find_spec` probe of the declared top-level packages for editable
installs (resolves the path without importing the package — no Django
settings are touched). Unions with `--workspace`/paths and dedupes on the
resolved document, so an editable install cannot make a module appear twice.

The counterpart gate, `--check` (rebuild in memory, byte-compare, non-zero
exit), already existed — it just was not wired anywhere. It belongs in the CI
of whichever repo commits the artifact.

### Changed — scaffolded modules ship their contract documents in the wheel

`[tool.setuptools.package-data]` in the library/module template now carries
`docs/capabilities.json`, `docs/flows.json`, `docs/errors.json` and
`CONFIG.MD`. This is what makes `--from-installed` possible at all: a module
that keeps its contract documents repo-only publishes code no agent reading
an installed environment can see. Verified by artifact, not by config — a
built wheel contains the four files, and `--from-installed` in a clean venv
with only that wheel reproduces the module's full index entry (operations,
errors, CONFIG rows). A `tests/test_new_library.py` case now pins it so the
scaffold cannot regress into producing a mute module.

### Removed — the repo-root `catalog.json` / `catalog.md` snapshot

Deleted, not refreshed. They were a hand-run 10-module aggregate with no
consumer anywhere in the fleet (grep-verified) and no gate — exactly the
artifact shape this release argues against. `stapel-tools` deliberately
commits no catalog of its own: the artifact belongs in the repo that
consumes it, behind that repo's `--check` gate.

## [0.22.1] — 2026-07-30

### Added — `surface` reaches `catalog.md`

`stapel-catalog`'s prompt-ready projection now carries a
`**Surface (call these):**` line per module — entry names + kinds, the same
compactness the extension-point line already has. Shipping a section that no
projection reads would have reproduced the exact defect the section exists to
fix. The curated `intent` of each entry stays in `capabilities.json` /
`--index`, where an exact-layer query can afford it. Absent entirely for a
module that declares no `surface_roots`.

## [0.22.0] — 2026-07-30

### Added — `surface`: the third section of `capabilities.json` (`stapel_tools.surface`)

`axes` describe the **configuration** surface ("what can be switched on");
`extension_points` describe the **substitution** surface ("what can be
replaced"). Neither answers the question a product author actually asks —
*"is there already a mechanism for X, and what do I call?"* — so six mechanisms
in one night were built, released, and never picked up: a permission class, a
safety gate, a published capability field, a nav template, a loader factory and
a set of error predicates. Not one of them was findable by a machine reading
the fleet's contracts, and four of the six live in `stapel-core`, which had no
contract document at all — not out of neglect: the format could only describe
axes and OpenAPI operations, and the core has neither.

`surface` is that third section: the **usage** surface. One entry per symbol a
product is meant to call, subclass, mount or read —
`{name, kind, path, intent, instead_of?, consumer?}`.

- **`kind` is a CLOSED vocabulary** of exactly the six observed genres:
  `permission_class`, `gate_function`, `template`, `predicate`,
  `capability_field`, `factory`. Closed on purpose — an open one becomes a heap
  of synonyms (`helper`/`util`/`function`) and a search over it stops working.
  It grows on the seventh incident, not in advance.
- **The entry set is DERIVED, never hand-listed.** A module declares
  `surface_roots` in `docs/capabilities.meta.json` — *scopes*, not symbols — and
  four closed selectors (`permission_classes`, `functions`, `capability_fields`,
  `templates`) expand them by AST. A new export inside a declared root shows up
  by itself and demands an intent; a deleted one takes its stale prose with it.
- **LOUD, not a warning: a selected export with no `intent` fails emission,
  naming the symbol.** A warning would be read zero times. A library that
  exports a symbol it cannot explain in one line has just built the next
  mechanism nobody adopts.
- **`name` is the entry's identity inside the module and is unique.** A
  capability field keeps its class prefix (`RegistrationCapabilities.email_mock`)
  — `email_mock` lives on both the registration and the login DTO, and those are
  two different published promises to two different screens.
- **`instead_of`** names the outside symbols an entry displaces
  (`IsNotAnonymousUser` → `rest_framework.permissions.IsAuthenticated`) — the
  fuel for a duplicate-of-surface check. **`consumer`** names who is obliged to
  read a published field (`email_mock` → `frontend`) — the fuel for a
  publisher-without-consumer check. Both checks are separate work; this release
  ships the data they need.

**Opt-in per module, on purpose.** A library with no `surface_roots` emits
byte-identical output to before, so the section turns no existing CI red and
buys nobody a rush of filler intent lines. What is *not* optional is the
inside of a declared root: once a module declares one, it can never again grow
an unexplained export there.

### Added — `stapel-surface`, for modules with no OpenAPI pipeline

`stapel-surface <repo>` emits a whole `capabilities.json` from the curated meta
layer alone (the `stapel-core` shape), `--patch` injects only `surface` +
refreshed `module`/`version` into a module's existing document (the
`stapel-agent` shape, whose document is still hand-written), and `--check` is
the byte-for-byte drift gate for both.

**`operations_total` is no longer mandatory.** It is emitted only when the
module actually ships a `docs/schema.json`. The core serves no catalogued HTTP
surface, and omitting the counter says that; a mandatory `0` would have been a
claim about the module rather than about the document.

## [0.21.0] — 2026-07-30

### Added — R008 (warning): a lifecycle/security flag inside `defaults=`

`get_or_create(..., defaults={"is_active": True})` reads as an assertion about
the object and is not one: on the *get* branch the dict is never touched, so
the caller silently accepts whatever the stored row says — an account an admin
deactivated, a revoked verification, a flag another service flipped.
`update_or_create` has the mirror problem: the dict IS applied to the found
row, so the same line silently rewrites the flag. Flags checked: `is_active`,
`is_verified`, `is_staff`, and the `*_required` family.

**Warning, and permanently so.** The pattern is often exactly right (a flag
that genuinely only seeds initial state — stapel-auth's TOTP re-enrolment
resets `is_active=False` on purpose), and an error-level rule on a legitimate
idiom gets `# noqa`'d wholesale, which costs more than it saves. The rule's
text names the canon instead of demanding a local fix: the invariant belongs
on the **point of use**, once — stapel-auth gates session issuance on the live
account state, so a deactivated user cannot get in no matter which
`get_or_create` created the row.

Fleet measurement before release: 4 findings across the stapel libraries
(auth 2, currencies 1, notifications 1), 3 in the legacy marketplace
checkout, 4 more in stapel-studio's vendored copies of the same files, 0 in
the product. The most interesting one is real: `stapel_auth/sso_service.py`
creates the SSO user with `is_active: True` in `defaults` — for an existing
deactivated account that value does nothing, which is precisely why the gate
has to be at session issuance.

### Added — CFG005: a library's CONFIG.MD row must name a knob the library has

CFG003 says "a CONFIG.MD row read nowhere in the project is stale" — and then
exempts every row owned by a stapel lib, on the assumption that the lib reads
it internally. Nothing checked that assumption, and it is exactly where the
defect lived: a switch documented as "turn it off without a deploy" that was
never introduced in the owning namespace at all, so it could not be turned
off. CFG001 was the wrong class for it (it catches a read outside settings;
here there was no read *anywhere*, which is the defect).

CFG005 is that missing mirror, and it runs where the assumption is checkable —
in the **library checkout** (a `stapel-*` `pyproject.toml`, no `manage.py`).
For every row under the library's own `## stapel-<lib>` owner section, the key
must exist in the library's code: in an `AppSettings(defaults=...)` namespace
(resolved through the `DEFAULTS = {...}` module constant every lib's `conf.py`
uses, one level into nested blocks), in a `declare_config`, in a config read,
or at minimum named as a literal / setting somewhere. Docstring and comment
mentions do **not** count — a key that appears only in prose is documented,
which is the thing being questioned.

Measured on the fleet before release: 0 findings across all 20 library repos
that ship a CONFIG.MD (the strict "must be in `defaults`" formulation alone
gave 85 — all false, from keys wired through helpers, module constants or flat
Django settings; the released rule reports none of them). Verified by planting
the original offending row back into `stapel-recordings/CONFIG.MD`: it reds by
name, and goes green again when removed.

### Added — CFG000: the registry law is no longer opt-out by omission

A project with no `CONFIG.MD` had CFG002/CFG003 **silently skipped** and a
green gate — the note saying so went to stderr, and downstream pre-commit
configs simply recorded the fact and moved on ("CFG002/CFG003 skipped
anyway"). CFG000 reports the missing registry as a **warning**: visible in
every `stapel-verify` run, still not failing a build that has not done the
CONFIG.MD sweep. Raised only for a unit that has configuration to register (a
`manage.py` service, a stapel distribution, or any file that reads a config
key) — a TS package or a spec repo has no registry to be missing.

Fleet count at release: 15 stapel repos + the product + 2 service checkouts.

## [0.20.0] — 2026-07-26

### Fixed
- **`stapel-nginx-cache-lint` no longer reports "clean" about a target it never
  read.** A path with no nginx conf printed the note "nothing to check" to
  stderr and "No SPA cache-canon issues found" to stdout — and the second line
  is what a reader, or a CI log scraper, sees. It now says "Checked 0 nginx
  confs … nothing was verified". Exit stays 0 (a library repo legitimately has
  no nginx conf), but a gate that reports success on zero inputs is the same
  defect class this linter exists to catch.

### Added — `stapel-nginx-cache-lint`, the SPA cache canon made machine-checkable

The canon (owner directive, 2026-07-26) is two opposite halves: the thin,
UNHASHED entry document must revalidate on every load, and content-hashed
build artifacts must be cached long and `immutable`. The scaffold template now
emits that shape — but a template only helps a project generated after it, and
the incident happened in a hand-maintained conf. This is the enforcement layer
for projects that already exist.

The incident it encodes (app.ironmemo.com): `location /` carried BOTH
`expires 1d` AND `add_header Cache-Control "public, must-revalidate"`. nginx
emits its own `Cache-Control` for `expires` and appends yours on top, so the
response carried two `Cache-Control` headers; a client combines them
(RFC 9111 §5.2) into `max-age=86400, public, must-revalidate` and honours the
max-age. A freshly deployed frontend fix stayed invisible for up to 24 hours —
and a live verification of that fix read the stale bundle and drew the wrong
conclusion.

Rules, over `service-configs/nginx*/`:

- **NGX001** (error) an entry-document / SPA-fallback location is cacheable.
- **NGX002** (error) a hashed-asset location is not `immutable` (or is cached
  for under a day).
- **NGX003** (error) a location emits both an `expires` directive and an
  explicit `add_header Cache-Control` — the double-header defect itself, on
  any location, cacheable or not.
- **NGX004** (warning) an entry document declares no cache policy at all, so a
  client may apply heuristic freshness (RFC 9111 §4.2.2).

`--live BASE_URL` additionally checks what a deployed stand ACTUALLY serves —
it fetches the entry document, asserts it revalidates, then follows the first
hashed asset the document itself references and asserts that one is immutable.
A fixed conf that was never deployed is still stale, and that is the half a
static check cannot reach.

Composed into `stapel-verify`, which every generated project's pre-commit
already runs — so an existing project picks the gate up on its next
stapel-tools upgrade, with nothing to regenerate. Models nginx's real
semantics: `expires off` emits nothing (the only value safe to combine with an
explicit header), `expires` is inherited from the enclosing block, and
`add_header` does NOT merge (any add_header in a location replaces the whole
inherited set). Deliberately silent on `proxy_pass` locations, on `/media`
(user uploads are never hashed) and on a bare `location /static/` (Django
collectstatic is content-hashed only under a manifest storage). Suppress a
justified exception with `# noqa: NGX00x` on the location line.

### Fixed — generated projects were red on their own `manage.py check`

No settings tier and no generated env file carried `FRONTEND_URL`, so every
project this generator produced failed `stapel_auth.E003` the moment it ran
with `DEBUG=False` — the monolith `check` gate, the boot-smoke tier, and a
client's very first prod boot. The check is right: every off-session redirect
the auth pair issues (SSO callback, magic link, QR account-conflict,
OTP-challenge continuation, security verification links) resolves against that
origin, and so do stapel-billing's Stripe checkout/portal returns and
stapel-notifications' links. The templates were wrong.

- `config/settings/base.py` (monolith/microservices) reads `FRONTEND_URL` from
  the environment with **no** fallback — base is what `prod.py` and staging
  star-import, and a dev default there is precisely the leak E003's hint warns
  about.
- The localhost fallback lives in `config/settings/dev.py` only; the minimal
  preset's single-module equivalent keeps it inside the existing non-prod
  branch, beside `SECRET_KEY`'s.
- `config/settings/boot_smoke.py` supplies its own gate-only value, the way it
  already seeds `SECRET_KEY` — that tier runs standalone with no env sourced.
- `.env.example`/`.env` (all three presets) carry `FRONTEND_URL` set to the
  project's declared public URL, documented; the committed dev `.env.local`
  carries the local origin.
- New gate: `manage.py check` under the **prod** tier fed the project's own
  generated `.env`, plus its negative twin — drop the key and E003 must fire
  again.

## [0.19.0] — 2026-07-22

### Added — media read-path auto-wiring (the frontend half of the descriptor)

When a scaffold wires a media source (`cdn`, or a `profiles` avatar), the
generated frontend now gets:

- `@stapel/image` as a dependency (`FRONTEND_IMAGE_VERSION`), so `<Image>` can
  render the `StapelImage` descriptor the backend denormalizes.
- An `AGENTS.md` §7 "Rendering images" rule — the one agents kept getting
  wrong: a media ref arrives as a `*_image` `StapelImage` (`avatar_image`, …);
  render it ONLY with `<Image meta={...}>` (measures the slot × DPR × aspect,
  blur-up), NEVER a bare `<img src>`/`<Avatar src>`, and never hardcode a tier
  or the 16px `preview_b64`. If a payload carries an image ref with no
  descriptor, the backend serializer is missing `stapel_core.media.image(...)`.

## [0.18.0] — 2026-07-20

### Fixed — generated monolith mismounted every feature lib (high-priority)

`new_service.py`'s `_url_include` (the function that renders each selected
Stapel feature lib's `path(..., include(<app>.urls))` row into a generated
service's `config/urls.py`) mounted EVERY lib under the hosting SERVICE's own
shared `{url_prefix}api/` (its slug, e.g. `"app/api/"` for a monolith) —
except `stapel_cdn`, hand-special-cased to its real `"cdn/api/"` mount. A
monolith combining more than one feature lib collided every one of them onto
the identical Django path; the generated frontend's api clients (`/<lib>/api/
v1/...`), the generated nginx proxy (per-lib `/<lib>/api/`) and the §57
reserved-paths canon all expected each lib at its OWN prefix, so a generated
fullstack project 404'd on every lib's API. The bug was invisible for a
dedicated single-lib microservice whenever the service's own slug happened to
equal the lib's key (the common case) — it only manifested the moment a
monolith combined libs, or a microservice's slug diverged from its lib's key.

- New `stapel_tools/_url_mounts.py` — the single source of truth for a
  lib's Django mount prefix, derived from `create_project.STAPEL_LIBS`
  (cross-checked lib-by-lib against each sibling checkout's actual urls.py/
  urls_v1.py, not merely trusted from the registry, and against the one
  hand-wired working reference, meettoday's own `config/urls.py`, for auth/
  workspaces/profiles/notifications/calendar/recordings/cdn). One documented
  outlier override (`stapel_translate`, whose own urls_v1.py hardcodes its
  full `"translate/api/v1/..."` prefix internally — mounts at the bare
  project root instead of doubling the segment).
- `new_service.make_context`'s `_url_include` now consults this map for every
  registered Stapel lib — `stapel_cdn` is no longer a hand-special-case, just
  one entry in the same general mechanism. A project-local/custom app not in
  the registry keeps the old shared-service-prefix fallback (no data to do
  better for it).
- `create_project._create_minimal`'s url-include rendering now goes through
  the same helper instead of a second, slightly different ad hoc default —
  one mechanism for monolith, microservices and minimal generation.
- Found and fixed while building the per-lib map: `STAPEL_LIBS["categories"]`
  and `STAPEL_LIBS["listings"]` declared a bare `"<mod>/"` mount (assumed to
  match calendar/video's "bakes api/ into its own urls" shape at onboarding
  time) but their own urls.py docstrings actually read like auth/cdn's — no
  internal `api/` segment, host must supply `"<mod>/api/"`. Fixed to
  `"categories/api/"` / `"listings/api/"` (this also fixed a latent,
  independent mismount in the `minimal` preset for these two libs).

### Verification

- `tests/test_create_project.py::TestMultiLibMonolithMountsEachLibUnderItsOwnPrefix`
  — a monolith with auth+profiles+calendar+cdn mounts each under its own
  prefix (not the shared service prefix), a real `django.urls.resolve()`
  reaches a view for a real operation path per lib (CI-safe auth+gdpr
  variant always runs; the fuller auth/profiles/calendar/cdn variant skips
  cleanly when those sibling packages aren't importable), and nginx-local/
  prod-nginx/Vite/`reserved-paths.json` all agree with the Django mounts.
- `tests/test_create_project.py::TestSingleAndNoLibScaffoldUnaffected` — a
  monolith with zero or one feature lib, and a standalone
  `stapel-new-service` microservice, still mount correctly (the shapes the
  bug was invisible for).
- `tests/test_registry_onboarding.py` / `tests/test_catalog_index.py` —
  updated for the categories/listings mount correction.

### Bump rationale

Minor (0.17.0 → 0.18.0): a correctness fix, but it changes generated output
(every non-cdn feature lib's url mount in a monolith with 2+ libs, plus
categories'/listings' mount in every preset).

## [0.17.0] — 2026-07-20

### Added — cdn auto-wiring

Generalizes the hand-applied meettoday avatar fix (11 hand-edited files) into
`stapel-create-project`'s monolith scaffold: selecting `cdn` in `--modules`
now auto-wires the FULL stack instead of only installing `stapel_cdn` as a
dependency — closing the "the cdn module exists, nothing serves it" gap
(every generated project would otherwise 404 on `/cdn/api/...` and 413 on
real uploads). Everything below is conditional on `cdn` being selected; a
project without it is byte-identical to the pre-fix scaffold.

- `new_service.make_context` — `stapel_cdn`'s url mount is now the literal
  `path("cdn/api/", include("stapel_cdn.urls"))`, not the generic
  `{url_prefix}api/` pattern every other feature lib shares in a monolith.
  Matches nginx's own GENERATED `^~ /cdn/api/` proxy (already built from
  `STAPEL_LIBS["cdn"]`'s default url_prefix) — without this, nginx forwarded
  `/cdn/api/...` to a Django that only knew `{url_prefix}api/...` for it, a
  guaranteed 404.
- `create_project.create_project` — auto-injects a self-documenting
  `STAPEL_CDN = {"ASSET_TYPES": ("avatar",), "ENABLED_SUBMODULES":
  ("images",)}` block (both are stapel-cdn's own library defaults; rendered
  explicitly so the generated settings state intent instead of silently
  relying on upstream defaults) whenever cdn is selected, plus
  `STAPEL_PROFILES = {"PROFILES_AVATAR_CHECK": "comm"}` when profiles is
  ALSO selected. Never overrides an explicit `--module-config` entry.
- `create_project._append_cdn_pip_requirement` — appends
  `stapel-cdn[images]>=<pin>,<<ceiling>` to the generated service's
  `requirements.txt`, independent of whether `stapel_cdn` itself lands via
  git submodule or pip: the `[images]` extra's native dependency (pyvips)
  is never satisfied by vendoring stapel_cdn's source alone.
- `_templates.DOCKERFILE_CDN` — a multi-stage `vips-builder` → runtime
  Dockerfile (mirrors `svc-stapel-studio/Dockerfile`, the verified libvips
  container precedent) selected instead of the plain single-stage
  `DOCKERFILE` whenever the service installs `stapel_cdn`, so `import
  pyvips` resolves at runtime without a compiler in the final image.
- `_frontend_templates.render_cdn_lib_ts` — writes
  `frontend/src/lib/cdn.ts` (a documented STOPGAP — no dedicated
  `@stapel/cdn-react` client pair exists yet) exporting `avatarUrlFor(ref)`,
  wired into `ProfileSettings` (`render_routes_tsx` — the LIVE mount path,
  since profiles always carries a nav mirror — and defensively into
  `render_modules_tsx`'s `ModulesPanel`) whenever profiles-react is also
  wired. `render_modules_tsx` additionally registers a stopgap `cdn`-keyed
  client in the generated `<StapelProvider clients={{...}}>`, reusing the
  primary pair's client — mirrors the hand-applied meettoday fix's
  `clients: { cdn: stapelClient }` — so core's `useStapelClient("cdn")` seam
  (called unconditionally by `ProfileSettings`' avatar-upload hook) never
  throws for want of a registered client.
- nginx's `client_max_body_size 50m;`/`location /media/` and the Vite dev
  proxy's `/media/` rule were ALREADY unconditional/generic (no code
  change needed) — the `/cdn/api/`+`/cdn/swagger/`+`/cdn/admin/` proxy rows
  were already GENERATED per-lib too (`_reserved_backend_prefixes`); this
  release adds explicit regression tests locking both in as this feature's
  own numeric gate.
- New tests: `tests/test_create_project.py::TestCdnAutoWiring` (8 cases —
  INSTALLED_APPS+url mount, settings block, module_config override, pip
  requirement, Dockerfile, ADO001 lint, byte-identical-without-cdn
  regression) and `tests/test_frontend_scaffold.py::TestCdnFrontendAutoWiring`
  (5 cases — client registration, avatarUrlFor wiring, cdn-without-profiles,
  nginx/vite proxy, byte-identical-without-cdn regression).
- Known follow-up (not built here): promoting `frontend/src/lib/cdn.ts`'s
  stopgap logic into a real `@stapel/cdn-react` client pair, which would
  drop the host-registered `cdn` client override entirely.

## [0.16.0] — 2026-07-20

### Added — scripted-fullstack navigation, scaffold half (Ф1)

The lib-side nav foundation (`@stapel/shell-react`'s `resolveNav`/
`<AppShell/>`, `@stapel/core`'s `NavEntry`/`PackageNavManifest` types, and
`nav-manifest.json` on auth-react/profiles-react/notifications-react)
shipped to stapel-react main but isn't published to npm yet. This release
is the SCAFFOLD half: `stapel-create-project` now generates a real
react-router v7 navigated app scriptedly (no LLM) instead of a single
unrouted `<App/>`, whenever `--auth`, `--landing`, or a selected module
with a mirrored nav surface is in play.

- `FRONTEND_ROUTER_DEPS` (`create_project.py`) — `react-router` pinned to
  the latest v7 release (`7.18.1`, verified via `npm view "react-router@^7"
  version` — the PLAIN `npm view react-router version` dist-tag is now a
  v8 major and would silently pull an incompatible one). Added to the
  generated `frontend/package.json` whenever routing is active.
  `FRONTEND_SHELL_REACT_PACKAGE`/`_VERSION` pin `@stapel/shell-react`
  ahead-of-npm (not published yet — 404s on npm today; pinned from the
  sibling stapel-react checkout's own package.json, same discipline as
  `STAPEL_LIBS`' `ahead_of_pypi` flag), added whenever a selected pair
  contributes nav entries.
- `FRONTEND_REACT_LIBS["auth"|"profiles"|"notifications"]["nav"]` — a
  manually PINNED MIRROR of each pair's own `nav-manifest.json` (auth.login
  + auth.security, profiles.settings, notifications.feed). New
  `scripts/check_nav_manifest_sync.py` — a drift gate (peer of the
  pin-verification comments) diffing the Python mirror against the sibling
  stapel-react checkout's real `nav-manifest.json` files; skips cleanly
  when that checkout isn't present.
- New generated files (`_frontend_templates.py`): `src/nav.generated.ts`
  (bakes `INSTALLED_NAV_MANIFESTS` at codegen time, computes `RESOLVED_NAV`
  by calling the real `resolveNav` against the committed
  `stapel.nav.json` at import time — the same call `<AppShell/>` itself is
  built on), `src/routes.tsx` (`createBrowserRouter` — react-router v7
  ships v6-future behaviour as its own default, no future-flags object to
  emit), `src/ProtectedRoute.tsx` (gates `/app` on
  `useActiveSessionReady`/`useAuthSessionState`, both already-published
  hooks — no auth-react change needed), `stapel.nav.json` (empty
  `{"overrides": {}}` override channel, deep-merge-over-default like
  `stapel.theme.json`), and `src/LandingPage.tsx` (`--landing` only, styled
  entirely through `cssVar("<role>")` §68 tokens, no raw hex).
- `main.tsx` mounts `<RouterProvider router={router}/>` (wrapped in
  `<ModulesProvider>` when any `@stapel/<module>-react` pair is also wired)
  once routing is active; a selection with none of `--auth`/`--landing`/a
  nav-bearing module collapses to the EXACT prior `<App/>` output, byte for
  byte (regression-tested).
- New CLI flags: `--landing` and `--auth`/`--no-auth` (default: derived
  from whether the `auth` module is selected).
- New `TestFrontendNavWiring` (`tests/test_frontend_scaffold.py`, 7 tests)
  plus 2 pre-existing `TestFrontendReactWiring` tests updated to select a
  non-nav-bearing module combo (their App.tsx/modules.tsx assertions no
  longer apply to a nav-bearing selection, which now routes instead).

Deferred to post-publish: an actual `npm ci && npm run build` against a
generated project — `@stapel/shell-react` isn't on npm yet, and
auth-react/profiles-react/notifications-react's last PUBLISHED releases
predate their `nav-manifest.json`/`NavEntry` core types. The import-graph
gate (every non-relative import resolves to a declared `package.json` dep)
covers everything short of an actual install today.

## [0.15.0] — 2026-07-19

### Fixed — monolith preset shipped no root controls surface (studio e2e-3f018cc3, R3/§44 follow-up)

Live studio runs run `make -C <assembled_root> lint/controls/test/boot-smoke`
at the generated project's ROOT regardless of preset. The minimal preset
always wrote a root `Makefile` with those targets; the monolith preset's
Django backend lives inside `svc-<slug>/` instead and never got a root
`Makefile` at all — every live monolith run failed the architect's lint gate
unfixably (the architect can't create root build files), blocking the
fullstack pipeline entirely. Closed, deletion-driven:

- New root `Makefile` for monolith (`MONOLITH_MAKEFILE`,
  `_compose_templates.py`) — `.PHONY: controls lint test boot-smoke`,
  `controls: lint boot-smoke test`, each target delegating into
  `svc-<slug>/`. Target names and `controls` semantics match the minimal
  preset's own Makefile 1:1, so the studio contract is preset-agnostic.
  Backend-only for now (a comment says so); `frontend/`'s own `npx eslint .`
  is a separate stage, not silently dropped.
- New `svc-<slug>/Makefile` (`SVC_MAKEFILE`, written by `scaffold_service` —
  also reaches every `stapel-new-service`-created service, monolith or
  microservices) exposing the same four targets standalone: `lint` runs
  `ruff check .`, `test` runs `pytest -q`, `boot-smoke` runs `manage.py
  check` under a new `config/settings/boot_smoke.py` tier.
- New `svc-<slug>/pyproject.toml` (`SVC_PYPROJECT`) — the service had NO
  ruff config before, so `ruff check .` (once the Makefile existed) would
  have run under bare defaults and flagged the Django settings tiers'
  intentional star-imports (`from .base import *`) as 25 false-positive
  F405/F403/E402/I001 errors. Selects the same rule set as the minimal
  preset (`E,F,W,I,B,UP`) with one addition: `config/settings/*.py` is
  exempted from F403/F405 (the star-import pattern is Django's own
  convention across settings tiers, not a bug).
- New `svc-<slug>/config/settings/boot_smoke.py` (`BOOT_SMOKE_SETTINGS`) —
  the monolith counterpart of the minimal preset's
  `config/settings_boot_smoke.py` (R3/§44 was minimal-only until now).
  Layers over `.base`, not `.dev`/`.local`: the dev tier adds
  django-debug-toolbar to `INSTALLED_APPS`, whose SQL panel unconditionally
  probes `django.contrib.gis` at `AppConfig.ready()` time — an environment
  fragility (observed as a raw `OSError` crash on a host with a
  broken/partial GDAL native lib, uncaught by the toolbar's own
  `except ImportError` guard) this gate must not inherit. Also seeds
  `os.environ["SECRET_KEY"]` with an insecure dev-only fallback when unset:
  `base.py` carries no fallback of its own (only `dev.py`'s does), and
  stapel_core's `config.E001` system check resolves required keys via
  `os.environ` directly — independent of any `django.conf.settings` value —
  so this gate must run standalone (no shell-sourced `.env`, no docker)
  straight after generation.
- `ruff>=0.4` added to the service's `requirements.txt` dev/test section
  (it was entirely absent — `make lint` had nothing to run against).
- Second generator defect found and fixed while getting the assembled
  monolith controls-green from birth: `BASE_SETTINGS`/`DEV_SETTINGS`
  (`_templates.py`) carried 4 extraneous f-string prefixes (F541) and 2
  import-order violations (E402); `ASGI_PY`/`WSGI_PY`/`URLS_PY`/
  `PROD_SETTINGS` carried unsorted import blocks (I001); `MODELS_PY`/
  `ADMIN_PY`/`MODULE_MODELS`/`MODULE_ADMIN` carried an always-unused import
  on a fresh scaffold (F401, silenced with an explanatory `# noqa`). All
  fixed at the template source, verified via a real `ruff check .` run
  (`All checks passed!`) against a freshly assembled monolith.
- Known residual gap: `make test`/`make controls` need a live Postgres
  (`docker compose -f docker-compose.local.yml up db` — same as any
  monolith dev workflow, not new) — not exercised by this fix's own test
  suite for that reason. microservices shares the missing-root-Makefile gap
  (confirmed: a fresh `stapel-example-microservices`-shaped assembly has no
  root Makefile either) but starts with zero services to lint until
  `stapel-new-service` is run, so it is not closed here — follow-up.

## [0.14.0] — 2026-07-19

### Added — frontend wiring: scaffold the selected `@stapel/<module>-react` pairs (owner directive, frontend-wiring gap)

The generated `frontend/` used to be a generic Vite+React shell that never
wired a project's selected feature libs' React counterparts, even when a
published `@stapel/<module>-react` pair existed for one. Closed, data-driven:

- New `FRONTEND_REACT_LIBS` registry (`create_project.py`) maps each
  `STAPEL_LIBS` key with a published pair (`auth`, `billing`, `calendar`,
  `notifications`, `profiles`, `recordings`, `workspaces` — versions pinned
  against both the sibling `stapel-react` checkout AND live
  `npm view @stapel/<name>-react version`, identical for all seven) to that
  pair's `create<Module>Runtime`/`<Module>Provider`/`register<Module>I18n`
  exports and, where genuinely zero-required-prop, its `/default` antd-skin
  top-level component (`AuthPanel`, `NotificationFeedList`,
  `ProfileSettings` — read off each pair's own prop interfaces, not
  guessed; `workspaces`' `/default` components all require a `workspaceId`
  the scaffold can't fabricate, so it stays provider-only).
- `frontend/package.json` gains the selected pairs' deps + `@stapel/core` +
  `@tanstack/react-query`, plus `antd`/`@stapel/tokens-antd` IFF a selected
  pair mounts a `/default` skin — never for a headless-only selection
  (billing/calendar/recordings ship no antd peer dep at all).
- New generated `frontend/src/modules.tsx` — the data-driven registry: one
  shared `<StapelProvider>` (first selected pair as the default client,
  every other pair via `clients={{ "<mod>": ... }}`, the exact multi-pair
  composition `@stapel/core`'s own README documents) wrapping one
  `<XProvider>` per selected pair (`ModulesProvider`), plus `ModulesPanel`
  mounting every selected pair's zero-config default component wrapped once
  in antd's `<ConfigProvider theme={toAntdThemeConfig("light")}>` (§68
  bridge). Regenerating this file is "change the module selection", never a
  hand-edit — adding a pair later is data, not code.
- `frontend/src/App.tsx` switches to a second, still-STATIC template
  (`APP_TSX_WITH_MODULES`) that imports `ModulesProvider`/`ModulesPanel`
  from `./modules.js` whenever the selection has >=1 react-paired module;
  a selection with none gets the byte-identical prior clean shell (no
  `modules.tsx`, no package.json churn — regression-tested).
- `tests/test_frontend_scaffold.py::TestFrontendReactWiring` — exact
  dependency-set assertions, version pins, provider/runtime wiring per
  selected pair, the zero-config-vs-provider-only default-component split,
  the import-resolves-to-a-declared-dep "compiles conceptually" gate, and
  the clean-shell regression for a selection with no react-paired module.

### Added — scaffold `stapel.theme.json` + `stapel-tokens`-bin pre-commit hook (§68 Ф5, color-token-matrix)

§68's neutral colour-role dictionary now reaches the `stapel-create-project`
monolith scaffold, so a freshly generated frontend starts with a real,
themeable colour source instead of hardcodes — and the generator is called
through `@stapel/tokens`' own published bin, never vendored:

- `frontend/stapel.theme.json` — the neutral role dictionary (`surface*`/
  `text*`/`border*`/`brand*`/`link*` + `success`/`warning`/`error`/`info` ×
  `{base, -bg, -border, -on}`), seeded with a sensible bluish `brand` and
  standard status colours, light+dark, in `_frontend_templates.THEME_JSON`.
- `frontend/package.json` gains the `@stapel/tokens` devDependency and
  `gen:tokens`/`gen:tokens:check` scripts calling the published
  `stapel-tokens` bin directly (`--targets core` by default — the default
  studio delivery is antd, self-sufficient; Tailwind stays opt-in, and if a
  project does add it the bin's `tailwind@4` `@theme` adapter is the target,
  never the legacy v3 RGB-triplet one).
- New `.pre-commit-config.yaml` hook `tokens-check` (frontend projects
  only) — `npm run gen:tokens:check` in `frontend/`, same
  regenerator-of-everything-that-can-be-regenerated shape as
  `config-manifest-check`/`reserved-paths-check`/`gen-client-check`; fails
  the commit on drift, auto-fix is `npm run gen:tokens` (no `:check`).
- `AGENTS.md`'s generated §5 (generated-artifacts table) and §6 (frontend
  rules) now spell out, in the generated project's own AGENTS.md, that
  colours live in `stapel.theme.json` → semantic roles, that **the default
  button colour is the `brand` role**, and how to re-theme (edit
  `ramps`/`core` → `npm run gen:tokens` or just commit — the pre-commit
  hook regenerates and gates drift).
- No forked/vendored copy of the generator anywhere in the scaffold
  templates — the exact `gen-tokens.mjs`/`tokens-lib.mjs` failure mode the
  color-token-matrix spec diagnosed in a live host is closed by
  construction (regression-tested: `TestThemeJsonScaffold::
  test_no_forked_generator_vendored_into_scaffold_templates`).

## [0.12.0] — 2026-07-17

### Added — `stapel-gen-client` + `stapel-docs`: the regenerator-of-everything pre-commit surface (owner directive)

Owner directive: "в pre-commit должен быть регенератор ВСЕГО, что можно:
клиентов (если был оверрайд), CONFIG.MD с полной сводкой энвов, документация
по api/флоу — в идеале двуязычная." CONFIG.MD/reserved-paths/PRESENTERS.MD
regeneration already existed (§57); this release closes the other two:

- **`stapel-gen-client`** — tier 2 of the two-tier answer to "our profile
  is overridden, its frontend pair needs to handle that"
  (`docs/pending/profile-fields.md` "Дополнение владельца" §17.07): a
  universal, non-library-specific command that regenerates a typed TS
  client from a PROJECT's OWN `schema.json` into
  `frontend/src/api/generated-override/<module>/schema.ts`, reusing
  openapi-typescript (the exact engine stapel-react's own
  `scripts/gen-api.mjs` already uses) via `npx` rather than reimplementing
  it. Gated on `override_active()` — a non-empty `STAPEL_SWAP = {...}`
  anywhere in the project, or an explicit `stapel.override.json`
  `"clientOverride"` flag — so the `gen-client-check` pre-commit hook is a
  silent no-op on every project that hasn't overridden anything yet, and
  comes alive automatically the day one does. `--check` is the drift gate,
  `--force` bypasses the gate for manual runs.
- **`stapel-docs`** — bilingual `docs/api.en.md` + `docs/api.ru.md`
  generation from a project's `schema.json`/`flows.json`/`errors.json`
  (endpoints + DTO fields sourced from backend docstrings, R004 canon; flow
  user-stories; error catalog). Where a module already ships a Russian
  translation (`translations/flows.ru.json`/`translations/errors.ru.json`
  — the stapel-translate precedent) it's used verbatim; otherwise the
  English text is shown with an honest `(en)` marker, never fabricated.
  Supports the monolith `codegen/generated/` aggregate (re-split into
  per-module sections by path prefix), per-service/vendored `docs/`
  checkouts, and the literal `<mod>/api/v1/schema.json` shape. `--check`
  is the `api-docs-check` pre-commit drift gate; a project with no
  `schema.json` yet is a graceful no-op.
- New shared discovery module `_docgen_scan.discover_modules()` — the one
  scanner both commands key off, so a project's doc sections and its
  client-override folders always agree on module names.
- `.pre-commit-config.yaml` template gains `api-docs-check` (every project
  type) and `gen-client-check` (frontend-carrying project types); AGENTS.md
  template gains a "Generated artifacts" table naming every regenerator and
  its source, plus a frontend bullet on the generated-override seam;
  README "Checks" section templates and this repo's own README document
  both new commands.

## [0.11.5] — 2026-07-17

### Added — `stapel-catalog --index`: the full machine index (agent-knowledge-base.md §64 "Волна 1")

`docs/pending/agent-knowledge-base.md` §64 found the catalog aggregator's
mechanism complete but its artifact never materialized on disk, and flagged
it as the cheapest unblock for the ADVISOR exact-layer (stapel-studio's
`studio_cto.advisor_index`, which already documented the consumer shape it
expects — this release is the producer side.

- `build_index()` extends `build_catalog()`'s per-module aggregate with,
  per module: `flows` (verbatim `docs/flows.json`, `[]` if the module hasn't
  documented any — an honest gap, not fabricated narrative), `errors`
  (verbatim `docs/errors.json`), `config_md` (CONFIG.MD table rows, key
  omitted for a module without one yet), `stapel_libs` (the
  `STAPEL_LIBS` registry's `url_prefix`/`requires`/`pin` for that module,
  omitted for an unregistered module) and, when a matching `-react` sibling
  package exists, `components` (operations/hooks/demos projected from its
  `manifest.json`).
- New CLI surface: `stapel-catalog --index -o catalog.json [--workspace W]
  [--react-root R]` emits the single-file full index; `--check` drift-gates
  either mode (index or the classic catalog.json+catalog.md pair) without
  writing.
- `catalog.json`/`catalog.md` are materialized at the repo root as a real
  snapshot of the current workspace (10 modules with a swept
  `capabilities.json` today) — the artifact existed only as an unrun
  mechanism before this release.

## [0.11.4] — 2026-07-17

### Fixed — ADO001 false positive on stapel-tools' OWN generated monolith

Three v0.11.x tags in a row (0.11.1/0.11.2/0.11.3) failed their own release-
gating `e2e-generated-project` CI job at the `stapel-verify` step:
`stapel-adoption-lint` reported `ADO001` ("module 'stapel_auth' is installed
and ships a urlconf but is not mounted") for every HTTP-capable feature lib
in a freshly generated monolith — even though `config/urls.py` genuinely
mounts each one (`path(f"{url_prefix}api/", include("stapel_auth.urls"))`,
this canon's own mount idiom — see `_templates.URLS_PY` /
`new_service.make_context`; the prefix is a runtime `settings.URL_PREFIX`
value, so the route is written as an f-string, not a plain string literal).

Root cause was in the linter, not the generator: `adoption_lint._route_literal`
only recognized `ast.Constant` route arguments, so an f-string route parsed as
neither a constant nor anything else it handled — `_walk_patterns` bailed via
its `raw_route is None` guard *before* ever inspecting the `include(...)`
target one argument over, silently dropping the mount from `ADO001`'s
`mounts` set.

- **`_route_literal` now also renders `ast.JoinedStr`** (f-string) routes:
  literal segments kept verbatim, each dynamic `FormattedValue` replaced with
  a `"{}"` placeholder (same normalization `re_path` regex groups already
  get) — enough for `ADO001`'s mount detection, and a reasonable
  best-effort route for `ADO002`'s duplicate-route check too.
- New regression coverage: `tests/test_adoption_lint.py` (mount via an
  f-string route) and `tests/test_create_project.py`
  (`TestGeneratedMonolithPassesAdoptionLint`) — the latter drives
  `create_project()` for real (monolith + `auth`) and asserts
  `stapel-adoption-lint` reports zero `ADO001` findings, so this specific
  generator/linter interaction can't silently regress again.

Verified locally end to end (assemble → `stapel-verify` 0 errors → live OTP
circle → frontend build → nginx circuit), including inside a clean
`python:3.12-slim` container mirroring the CI job, before retagging.

## [0.11.3] — 2026-07-17

### Fixed — reserved-prefix canon: a module's bare root belongs to the frontend

Live-run collision (owner report): a generated nginx/Vite rule reserved a
selected lib's ENTIRE prefix (`location /calendar/ { proxy_pass ...; }`),
silently swallowing an identically-named frontend SPA page (`/calendar` —
the calendar view). Root cause: `_reserved_backend_prefixes` reserved a
module's bare root defensively ("so a future root-mount lands already
proxied"), which is exactly what a frontend router also needs.

- **Reservation narrowed to named sub-surfaces**: `/<mod>/api/`,
  `/<mod>/swagger/`, `/<mod>/schema.json`, `/<mod>/admin/` — the sub-surfaces
  our canon's generic per-service URLconf (`URLS_PY`) would mount if that lib
  became its own service — never the bare root or an arbitrary sub-path
  (both stay the frontend catch-all's). `admin`/`staticfiles`/`media`/the
  project's own slug keep their full-subtree reservation (unchanged — those
  genuinely own their whole namespace today). Applies to all three
  consumers: local-nginx (`^~`/`=` locations on the sub-surface), prod-nginx
  (same), and the Vite dev proxy.
- **New `reserved-paths.json`** at the generated project root — the single
  source every consumer above renders from, schema agreed with
  `@stapel/eslint-plugin`'s `no-reserved-backend-route` rule:
  `{"reservedPathPrefixes": [...]}`, a flat array of `/`-leading prefixes.
  The generated `frontend/eslint.config.js` points
  `settings.stapel.reservedPathsFile` at it; `@stapel/eslint-plugin` +
  `eslint` are now frontend devDependencies.
- **New `stapel-reserved-paths` CLI** (`--check` for the pre-commit drift
  gate, no-flag to regenerate) — wired into a monolith's
  `.pre-commit-config.yaml` as `reserved-paths-check`, next to
  `config-manifest-check`/`presenter-catalog-check`.
- **AGENTS.md**'s reserved-namespace rule reworded: a lib's bare `/<mod>`
  root is the frontend router's; only its named sub-surfaces are the
  backend's.
- **CI**: the live nginx circuit (`ci.yml`/`publish.yml`) now asserts a
  module's bare root resolves to the frontend (200, not a redirect into the
  backend reservation) and its `/api/` sub-path still resolves into the
  (down, in this nginx-only circuit) backend reservation (502 — proves
  routing, not content).

## [0.11.2] — 2026-07-17

### Changed — env/deploy canon revision (owner decisions after the live run)

- **`.env.local` is COMMITTED** (renamed from `.env.dev` — "dev" reads as the
  dev STAND; this file is strictly the local machine). Deterministic,
  recognizably dev-marked values only: `django-insecure-dev-*` SECRET_KEY/JWT
  (the prefix stapel-core's prodguard already refuses at prod boot — no new
  core mechanics), `POSTGRES_PASSWORD=stapel` (refused by guard_db_password),
  admin/admin superuser, and an explicit `STAPEL_LOCAL_ENV=1` flag. Clone →
  `docker compose -f docker-compose.local.yml --env-file .env.local up` works
  with zero manual config. The stand names `.env.dev`/`.env.stage`/`.env.prod`
  are RESERVED and gitignored (generated per stand, never committed).
- **`deploy/` scripts generated with a hard gate** (`deploy/deploy.sh` +
  `deploy/check-env.sh`, monolith + microservices): deploy refuses any env
  carrying dev markers (`STAPEL_LOCAL_ENV`, `django-insecure-*`/
  `dev-insecure-*`/`change_me*` secrets, `DEBUG=true`, default passwords,
  `*_PROVIDER=mock`, non-prod `DJANGO_ENV`) with a clear "сгенерируйте боевой
  env" error. The same values are refused at boot by core's prodguard —
  script fails BEFORE containers restart, guard covers bypasses.
- **Compose naming scale**: local stack = `docker-compose.local.yml` (was
  .dev); dev/stage/prod compose names reserved for stands (prod stays
  `docker-compose.yml`). nginx-local dir + FRONTEND_LOCAL_UPSTREAM renamed to
  match.
- **`docker-compose.local.yml` is SELF-CONTAINED** (no `include:` of base) —
  root fix for the v0.11.0/0.11.1 CI failures: several compose versions
  reject overriding an included service ("services.nginx conflicts with
  imported resource"). Local volumes are `-local`-suffixed; local nginx
  mounts its envsubst template (`default.conf.template` — must have exactly
  that name to overwrite the image's default site) at /etc/nginx/templates
  only, defaults to port 8080.
- **nginx canon (owner, live-run root-causes)**: `proxy_set_header Host
  $http_host` (not `$host` — strips the port), `absolute_redirect off;` in
  every generated server block (nginx's own /admin → /admin/ redirect bakes
  in the internal port 80 and loses the external mapping; port_in_redirect
  does not fix it), and deferred upstream resolution (`set $stapel_backend
  …; proxy_pass $stapel_backend;` — a literal host refuses to START while
  the backend container is down).

### Added — §55 presenter discipline in the generators

- `new-library`/`new-module` scaffold `presenters.py` (default presenter +
  `declare_swap()` + `get_<x>_presenter()`; DTO instantiated ONLY there);
  view templates go through `get_presenter` — a generated project passes
  stapel-verify (SWAP001/SWAP002 included) from scratch, proven by test.
- `create_project`/`assemble_scaffold` generate the project-root
  `PRESENTERS.MD` through stapel-core's exported `write_presenters_md()`
  hook (best-effort with a manual-command note when core isn't importable);
  new `presenter-catalog-check` pre-commit hook (`manage.py
  presenter_catalog --check`) keeps it fresh.
- AGENTS.md §2: imperative CORRECT/WRONG snippet pair (get_presenter vs
  direct DTO in a view).

### Added — generative prefixes + the E2E "оно едет" CI gate

- nginx locations (local + prod) and the Vite proxy table are GENERATED from
  the selected libs (STAPEL_LIBS url_prefixes + slug + admin + static/media)
  — one list, three surfaces; the "forgot /calendar in the proxy" bug class
  is unrepresentable.
- New CI job `e2e-generated-project` (ci.yml + publish.yml, release-gating):
  stapel-assemble monolith (auth+notifications) with green gates →
  stapel-verify=0 → live circle via `scripts/e2e_live_circle.py` (migrate →
  register → OTP code read from the LOG (mock canon) → verify → REGISTERED →
  authenticated /me 200) → `npm install` + vite build of the generated
  frontend → compose config validity → live nginx circuit (`/e2e` without a
  slash → 301 with a RELATIVE Location — the redirect-port regression,
  pinned forever).
- Fix found BY the e2e circle: the scaffold never generated
  `config/celery.py`, so every `@shared_task` in an installed lib bound to
  Celery's default unconfigured app (amqp://localhost) — stapel-auth's
  login-notification `.delay()` 500'd the login. Now: standard celery app
  module + `config/__init__.py` binding (all presets) +
  `CELERY_TASK_ALWAYS_EAGER` in dev/minimal settings (no broker needed
  locally).
- Fix (studio-integration finding, root): `assemble_scaffold`'s check gate
  hardcoded manage.py at the project root — every `--type monolith` assembly
  was `result.ok=False` by construction. The gate now resolves cwd by
  project type (svc-<slug>/, config.settings.base, project .env loaded);
  studio's local workaround can be removed.
- CFG004 (warning): CONFIG.MD row with an empty Purpose column;
  `stapel-config-manifest` CLI (--check/regenerate) + `config-manifest-check`
  pre-commit hook (both landed with the 0.11.x wave, documented here).

## [0.11.1] — 2026-07-17

### Fixed
- Retried the 0.11.0 PyPI publish: the tag build failed on a resolver
  conflict (installs `stapel-gdpr`/`stapel-auth` from git main alongside
  `stapel-core` from git main; `stapel-gdpr`'s pyproject still pinned
  `stapel-core<0.11` at the time). Cleared now that the 0.11 fleet re-pin
  has landed on `stapel-gdpr` main (0.3.8) and `stapel-auth` main. No
  functional change.

## [0.11.0] — 2026-07-17

### Added — §57 dev/prod compose + nginx canon, entrypoint canon, AGENTS.md, pre-commit README canon, dev-env canon, CONFIG.MD regeneration hook

Live-run postmortem, owner directive package. New generator surfaces for
`stapel-create-project --type monolith` (the "recommended" preset — scope
for this pass; microservices/minimal frontend wiring is a tracked follow-up).

- **Dev/prod compose + nginx (§57 item 1).** `frontend/` — a real Vite +
  React + TypeScript scaffold (`stapel_tools/_frontend_templates.py`) — is
  now generated alongside the backend. `docker-compose.dev.yml` starts the
  Vite dev server (`frontend`, hot reload, logs visible) + the Django
  backend (now actually booting `config.settings.dev`, not the baked-image
  prod default — see the entrypoint-canon fix below) + a dev-nginx that
  proxies the reserved backend namespace (`/<slug>/`, `/staticfiles/`,
  `/media/`) to Django and everything else to Vite. `docker-compose.yml`
  (prod) gains a one-shot `frontend-build` service that populates a
  `frontend-dist` volume the main nginx serves as static files (SPA
  fallback). Proxy targets are env vars with compose-network defaults
  (`BACKEND_UPSTREAM`, `FRONTEND_DEV_UPSTREAM`), overridable via `.env`/
  `.env.dev` for a native run — nginx's own envsubst-on-templates feature
  renders `service-configs/nginx-dev/nginx-dev.conf.template`.
- **Static/media collision check (§57 item 2, answered).** No collision:
  monolith already namespaces `STATIC_URL`/`MEDIA_URL` per service slug
  (`/staticfiles/<slug>/`, `/media/<slug>/`), and every backend route lives
  under its own `/<slug>/` prefix — a frontend router must simply not claim
  those reserved prefixes, which nginx enforces by prefix-match specificity
  (documented in `NGINX_CONF`'s comments and the generated `AGENTS.md` §3).
- **Entrypoint canon (§57 item 3).** `bootstrap.sh` now runs
  `createsuperuser --noinput` (Django's own env-driven flow —
  `DJANGO_SUPERUSER_USERNAME/EMAIL/PASSWORD`) between migrate and
  collectstatic — no project-specific Python, no model imports (closes the
  live-run defect: a hand-rolled entrypoint importing a since-deleted model).
- **`AGENTS.md` (§57 item 4).** `create_project` emits a base OSS
  coding-rules file at every project root (`_agents_template.py`):
  StapelResponse/ERR_*/@flow_step (R001-R007), get_model/get_presenter
  indirection (SWAP001/002), config-in-one-place + purpose (CFG001-004),
  URLField max_length (URL001), and — for a monolith's `frontend/` — the
  `@stapel/eslint-plugin` rule set (no raw colours/fetch/storage, typed
  events, i18n-key existence).
- **Pre-commit README canon (§57 item 5).** Every project type (plus
  `new-library`) gets a `.pre-commit-config.yaml` (`_precommit_templates.py`)
  running `stapel-verify` (+ `eslint` for a monolith's frontend) and a root
  README "Checks" section documenting `pip install pre-commit && pre-commit
  install`.
- **Dev-env canon (§57 item 7 — owner follow-up).** `.env.dev` is generated
  with real secrets (not placeholders): DB/comm-bus (inline by default, never
  assumes Kafka), `DJANGO_SUPERUSER_*` for the entrypoint canon, Vite/backend
  proxy targets. On a local stand, **mock providers are on by default**:
  stapel-notifications already defaults `EMAIL_PROVIDER`/`SMS_PROVIDER` to
  `"mock"`; when stapel-auth is selected, `config/settings/dev.py` now also
  sets `STAPEL_AUTH["USE_MOCK_SMS_OTP"/"USE_MOCK_EMAIL_OTP"] = True` (booleans
  in stapel-auth's `no_env` list — only settable this way), so OTP codes are
  logged, never sent — registration/login is completable by reading the
  service log. A new `--env-preset` (`standalone` default, `studio`) picks
  the channel-origin preset; `studio` adds DOCUMENTED STUB keys (generic
  email sender, "Login via Stapel Studio" OAuth) with `TODO(§57 studio
  preset)` markers — no sender/studio-OAuth infrastructure exists yet, this
  is only the shape of the future preset. Threaded through
  `create_project`/`assemble_scaffold`.
- **CONFIG.MD regeneration hook (§57 item 8 — owner follow-up).**
  `stapel-config-manifest` (new CLI, `config_manifest.py`) regenerates a
  project's root `CONFIG.MD` from its libs' own registries — `--check` fails
  (drift) without writing, no flag regenerates + exits 0 for `git add`.
  Wired into every generated `.pre-commit-config.yaml` as
  `config-manifest-check`. New **CFG004** (warning, `config_lint.py`): a
  `CONFIG.MD` row with an empty Purpose column — closes the "documented in
  name only" gap CFG001-003 didn't cover; promotes to error once the
  per-lib CONFIG.MD sweep completes (DOC001's posture).
- Bug fix found auditing the above: `stapel-new-service`'s compose-file
  containment check false-positived against the monolith/microservices
  templates' own commented example (`"  # svc-app:"`), silently leaving a
  project's first/default backend service never actually wired into
  `docker-compose.yml`/`docker-compose.dev.yml`. Fixed to match a real
  service key line, not a substring.
- Bug fix: `assemble_scaffold`'s static gates (`manage.py check`/boot-smoke)
  now load the generated project's own `.env` into the subprocess
  environment — needed since stapel_core's newer `config.E001` system check
  resolves `required` CONFIG.MD keys (e.g. `SECRET_KEY`) against the actual
  process environment, independent of any settings.py fallback.

## [0.10.4] — 2026-07-16

### Added — v1 canon in the scaffolds (§60, api-versioning.md §2)

- `new-library` (module kind) now scaffolds `urls_v1.py` from day one: the
  root `urls.py` is a thin `api/v1/` mount, the actual URL set lives in
  `urls_v1.py`, the ping example serves at `/<slug>/api/v1/ping`. No bare
  `/<mod>/api/...` variant exists — canon, not a choice.
- `new_module` (service-embedded app) scaffolds the same split:
  `urls.py` mounts `api/v1/` → `urls_v1.py`.
- `new-react-lib` default `path_prefix` → `/<module>/api/v1/`
  (`MANIFEST_TAGPREFIX` follows).
- (also shipping in this release: the §55 SWAP001/SWAP002 + DOC001 lints and
  the STAPEL_LIBS composite registry entries listed under their own headings
  below — committed on main since 0.10.3.)

### Added — SWAP001/SWAP002 + DOC001: the §55 anti-lock-in lints

- New `stapel-swap-lint` (`stapel_tools/swap_lint.py`), two error-level rules
  (`docs/pending/extensibility-presenters.md` §1/§6 — the django-oscar #3232
  bug class):
  - **SWAP001** — direct import (or import-and-instantiate) of a class that
    is registered as the `default=` of a `get_model()`/`get_presenter()`
    call anywhere in the scanned tree (`stapel_core.django.swappable`,
    STAPEL_SWAP registry). Registry is built statically in one AST pass
    (every accessor call's dotted `default` string literal), violations
    found in a second pass over `from X import Y` bindings — no Django
    execution. A stray direct import silently defeats a host's config-swap
    for that call site; this makes the discipline machine-checked.
  - **SWAP002** — a `views.py` instantiating a `@dataclass` DTO imported
    from a `dto.py` module directly, bypassing the presenter
    (`get_presenter(...)` → `.present(...)`). Only cross-module `dto.py`
    imports are in scope (a local view-only dataclass is not the presenter
    contract); `tests/` and `test_*.py` are excluded for both rules
    (fixtures/factories legitimately build concrete classes).
  - False-positive posture: unresolvable imports (`import pkg.mod` +
    attribute access) resolve toward NOT flagging — opposite of URL001's
    default, because a false positive here blocks a legitimate
    definition/consumer file, not a width choice. `# noqa: SWAP001`/
    `# noqa: SWAP002` escapes supported.
- New `stapel-doc-lint` (`stapel_tools/doc_lint.py`):
  - **DOC001** (warning, the spec's "DOC-FIELD") — a Django model field with
    neither `help_text=` nor a `#` comment on the line above. Warning, not
    error: the legacy surface is large (74 findings on stapel-core alone at
    introduction), same W-before-E rollout as R100. Undocumented fields are
    a silent gap in the presenter auto-catalog (§4) and generated OpenAPI
    schema (§2). `@dataclass` DTO docstrings stay R004's job (`lint.py`) —
    DOC001 never scans `dto.py`. `--strict` flips warnings to exit 1.
- Both wired into `stapel-verify` as sections 6 and 7 (`run_swap_lint`,
  `run_doc_lint`); console scripts `stapel-swap-lint` / `stapel-doc-lint`
  registered.
- Tests: `tests/test_swap_lint.py` (registry build, direct import, direct
  instantiation, accessor-path clean, tests-dir exclusion, noqa, SWAP002
  positive/negative incl. local-dataclass and non-views exclusions),
  `tests/test_doc_lint.py` (help_text pass, preceding-comment pass, noqa
  forms, FK/relation fields, manager/constant non-fields, dto.py exclusion,
  migrations skip), `tests/test_verify.py` extended to 7 linters.

## [0.10.3] - 2026-07-16

### Added — `stapel-verify`: one gate running the whole lint arsenal

- New `stapel-verify <project_root> [--workspace ROOT ...] [--base-sha SHA]
  [--json]` CLI (`stapel_tools/verify.py`). Pure composition — reuses each
  existing linter's own public entrypoint (`lint.scan_paths`,
  `adoption_lint.lint_project`, `url_lint.lint_paths`,
  `config_lint.lint_project`, `migration_lint.lint_paths`) and adds no new
  checking logic of its own.
- Motivation: a project's CI can be green on a generic linter while R006
  (`StapelResponse({...})` raw dict, skipping the serializer) and ADO002
  (a hand-rolled route shadowing an operation the installed module already
  ships) sit unexercised — not because the rules don't exist, but because
  nothing wires all the linters into the pipeline that actually runs.
  `stapel-verify` is the mechanical answer: one command, the entire arsenal,
  exit 1 if any of them found an error.
- Output: a summary table (linter → errors/warnings), full findings from
  every linter, and a machine `--json` form (per-linter errors/warnings/
  findings) for agents/CI. `--workspace`/`--base-sha` are forwarded to the
  sub-linters that accept them (adoption-lint, migration-lint).
- Console script registered in `[project.scripts]`.
- Tests (`tests/test_verify.py`): a fixture project with a deliberate
  violation for every composed linter (R006, ADO001/ADO002/ADO004, URL001,
  CFG001, MIG001) asserting each linter contributes to the aggregate report,
  a clean-project all-zero case, CLI exit codes (0/1/2), `--json` shape, and
  `--workspace` forwarding.
- **Fixed a latent bug found by this integration**: `adoption_lint.py`'s
  ADO002 findings stored a `Path` object (from the `urlconf_by_route` map)
  as `.path` while every other rule stores a `str` — `findings.sort()`
  crashed with `TypeError: '<' not supported between instances of
  'PosixPath' and 'str'` whenever ADO001 and ADO002 both fired on the same
  project, a combination its own test suite never exercised together.
  One-line fix: `str(uf)` at the point of insertion.

## [0.10.2] - 2026-07-16

### Fixed — CI: `TestAuthSubfeatureAxes` depended on a workspace sibling not present in an isolated checkout

- `test_unknown_auth_axis_is_a_hard_error_not_silently_passed_through` (and
  its siblings in `TestAuthSubfeatureAxes`, `tests/test_assemble_scaffold.py`)
  validated `STAPEL_AUTH` config keys against the real
  `stapel-auth/docs/capabilities.json`, resolved via
  `_module_config._default_workspace_root()` as a sibling directory of this
  repo's own checkout. That sibling exists in the shared dev workspace but
  not in the publish-workflow's isolated single-repo checkout (`stapel-auth`
  is pip-installed there for importability, which does not recreate the
  sibling *directory* layout the validator looks for) — so
  `known_config_keys` silently fell back to its warn-and-pass-through path
  and the hard-error assertion never raised, failing the gate that blocked
  `v0.10.1`/`v0.10.2` publishing.
- Fixed the test design, not the check it exercises: `TestAuthSubfeatureAxes`
  now carries an autouse fixture that builds a tmp fixture mini-registry
  (`stapel-auth/docs/capabilities.json` with exactly the axes the class
  references) and monkeypatches `_default_workspace_root` to it, so
  validation is genuinely exercised — unknown axis still a hard error —
  without depending on any sibling checkout. Same pattern already used
  correctly by `test_create_project.py`'s `TestModuleConfigValidation`.
- Audited `tests/` for the same disease (absolute/`../` paths, sibling-repo
  reads outside `tests/fixtures/`); no other instance found — every other
  `stapel-*` string reference in the suite is either a `tests/fixtures/`
  file, a hardcoded registry-pin/rendered-content assertion, or already
  workspace-fixture/`pytest.skip`-guarded.
- Verified packaging: `url_lint`/`config_lint`/`config_manifest`/
  `assemble_scaffold` (and every other `stapel_tools/*.py` module) land in
  the built wheel — `stapel_tools` is a flat package with no subpackages, so
  `[tool.setuptools.packages.find]`'s package-level discovery isn't exposed
  to the explicit-subpackage-list-lags-behind class of bug that hit
  `stapel-core`'s `projections`.

## [0.10.1] - 2026-07-14

### Fixed — CI: `v0.10.0` tagged but never published (pre-existing gap)

- `v0.10.0`'s publish run failed: `test_minimal_with_auth_still_resolves_to_stapel_user`
  and two `assemble_scaffold` auth-axis tests assemble a project with the auth
  module and import `stapel_auth` in a subprocess — only importable on this
  dev workspace (every stapel-* module editable-installed as siblings), never
  in CI's isolated checkout, which installed `stapel-core` but not
  `stapel-auth`/`stapel-gdpr`. Confirmed pre-existing (same failure on
  `ci.yml` since before 2026-07-09). Added both to the `Tests` step's install
  line, same pattern already used for `stapel-core`.

## [0.10.0] - 2026-07-14

### Added — `stapel-url-lint`: bare Django `URLField()` gate (library-standard.md §3.8)

- New `stapel-url-lint [paths...]` CLI (`stapel_tools/url_lint.py`), in the
  `stapel-migration-lint` / `stapel-config-lint` idiom (rule codes, `--json`,
  `--strict`, exit 1 on any error).
- **URL001 (error)** — `models.URLField(...)` (Django ORM field, including a
  bare `URLField(...)` bound to `django.db.models` via `from
  django.db.models import URLField`) with no explicit `max_length` keyword.
  Django's implicit default is `varchar(200)`, which real external URLs
  (OAuth avatar, IdP SSO/OIDC discovery, webhooks) routinely exceed —
  degrading from a validation-time problem to a `StringDataRightTruncation`
  500 on INSERT (incident: OAuth signup crash on a Google avatar URL > 200
  chars; fixed in `stapel-core` 0.10.1 + `stapel-auth` 0.5.5). Suppress a
  deliberate exception with `# noqa: URL001`.
- `rest_framework.serializers.URLField` (and other DRF field classes) are
  excluded by design — a `CharField` with no implicit `max_length` and no
  backing DB column, so the truncation bug this rule guards against cannot
  occur there. Detection is import-alias based; a `URLField(` bound to an
  unrecognized qualifier defaults to flagged rather than silently passing.
- Migrations directories are skipped (the model source is the single place
  to fix; flagging the generated migration too would duplicate the finding).

### Added — `stapel-adoption-lint`: honesty gate for stapel-module adoption (BACKLOG §26/§30/§32, §35)

- New `stapel-adoption-lint <project_dir>` CLI (`stapel_tools/adoption_lint.py`),
  in the `stapel-migration-lint` idiom (rule codes, `--json`, `--strict`, exit 1
  on any error). It fails the ways a module gets "adopted" on paper but not in
  fact.
- **ADO001 (error)** — a stapel module is installed (in `requirements*.txt` or
  `INSTALLED_APPS`) and ships a urlconf, but its urls are not mounted in the
  project's ROOT_URLCONF (no `include("stapel_<mod>.urls")`); its endpoints
  don't exist. Deliberate headless use is declared with a file-level
  `# stapel: headless <mod>` marker (short or full package name) in the urlconf
  or a settings file. Library-only modules (no `urls`) are never flagged.
- **ADO002 (error)** — a project-owned urlpattern duplicates an installed
  module's operation: its route, normalized (so `<int:pk>` ≡ `{id}`), equals a
  path the module publishes in `docs/schema.json` (OpenAPI). The finding names
  the shadowed operation(s). Schemas are read next to the installed package
  (`importlib` spec — editable/dev installs and the neighbour-repo workspace
  layout) or a sibling `stapel-<mod>/docs/schema.json`; when none is discoverable
  the check is skipped for that module with a note (never a false error).
- **ADO003 (warning)** — a `STAPEL-MIGRATION.md` records *done* work but the
  current git branch is neither `main`/`master` nor merged into it (a finished
  migration lingering off `main`). Git-only, no network.
- **ADO004 (warning)** — a `requirements` pin is never imported anywhere in the
  project (dead pin; canonical case `PyJWT`, correctly resolved to its `jwt`
  import via `packages_distributions()` + a small alias table). stapel modules
  (referenced by dotted string), a small entry-point-only runtime/tooling
  allowlist (servers, DB drivers, test/lint tools), and packages configured by
  string in settings (INSTALLED_APPS/backends) are exempt.
- Deliberate parsing limits are documented in the module docstring: mounts are
  recognised only from literal `include("<pkg>.urls")` strings and inline-list
  includes (opaque/dynamic includes need the headless marker); custom routes are
  gathered from the ROOT_URLCONF file(s), not from app-level urlconfs reached via
  a string include; `re_path` regexes are normalised best-effort; ADO004 judges
  only dists whose import names resolve, and sees only the project's own tree (a
  dep used solely transitively by a module reads as a dead pin — don't pin your
  dependencies' dependencies). Covered by `tests/test_adoption_lint.py` (mounted
  / unmounted / headless-marker / inline-include; duplicate-route + param
  normalization + no-schema skip; dead pin + imported/stapel/settings-string/
  runtime-only/unresolvable exemptions; the git branch gate; CLI/JSON/exit codes).

### Added — `stapel-catalog`: module-catalog aggregator (BACKLOG §33 p.1)

- New `stapel-catalog` CLI (`stapel_tools/catalog.py`) that aggregates every
  module's `docs/capabilities.json` (the fourth contract artifact) into two
  catalog artifacts: `catalog.json` — the full machine aggregate (every source
  document verbatim + roll-up totals + curated recipes) — and `catalog.md` — a
  compact, prompt-ready projection (header roll-up, then per module: name,
  version, `provides` one-liner, an axis table `key | default | ops gated`,
  extension-point names, requires).
- Inputs are explicit module repo paths (or direct `capabilities.json` paths)
  and/or `--workspace <dir>`, which scans `stapel-*/docs/capabilities.json`.
  A source with no artifact, malformed JSON, or no `module` field is skipped
  with a warning — never a crash; a partial catalog still emits.
- Curated **recipes** (composite projections — a marketplace = N modules) are
  read from a separate `--recipes <file>` and rendered as their own catalog.md
  section. The minimal recipe schema (a restricted, dependency-free YAML
  subset — `recipes:` list of `{name, summary, modules, notes}`) is documented
  in the module docstring; a malformed recipes file is a loud error (curated
  input, not a discovered artifact).
- Both artifacts are deterministic (modules sorted by name, axes by key, no
  timestamps) so `catalog.md` is stable enough to commit into other repos'
  system prompts. Covered by `tests/test_catalog.py` (fixture capabilities of
  every shape — full / minimal / broken JSON / absent; byte-for-byte
  determinism across two runs).

## [0.10.0] — 2026-07-10

### Changed — generated-project layout aligned with the community canon (BACKLOG §29)

**Breaking for generated projects** (the scaffolders' output changes; the CLI
surface is unchanged). Regenerate, or rename by hand in existing projects.

- **Settings package `core/` → `config/`** across every preset (minimal,
  monolith, microservices). `ROOT_URLCONF`, `WSGI_APPLICATION`,
  `DJANGO_SETTINGS_MODULE`, the WSGI/ASGI modules, `Dockerfile`/compose
  `gunicorn`/`celery` targets, `manage.py`, `pytest.ini`/`pyproject`, and the
  isort `known-first-party` list all now point at `config`. Matches
  cookiecutter-django / HackSoft / Two Scoops, and drops the name collision
  with the `stapel-core` package. The monolith/microservices settings split
  keeps its existing file set (`base`/`dev`/`local`/`prod`) — only the package
  name moved.
- **User modules live under `apps/` uniformly, as a regular package.** Every
  scaffolded app — the starter module in a service AND anything added by
  `stapel-new-module` — is now `apps/<module>` with `apps/__init__.py` present,
  `INSTALLED_APPS = ["apps.<module>"]`, and `AppConfig.name = "apps.<module>"`
  (full dotted path, Django ticket #24801). Fixes the layout bug where a
  monolith's first module was created top-level while `stapel-new-module`
  placed later ones in `apps/` (incompatible paths in one fresh service), and
  where the minimal preset's `apps/` had no `__init__.py` (namespace-package
  edge case). `stapel-new-module` now also writes `apps/__init__.py`
  defensively. Follows the wemake-django-template pattern.
- The minimal preset keeps its single `config/settings.py` (deliberate — the
  falco / Adam Johnson camp for a small, no-Docker project); only larger
  presets get the settings split.

## [0.9.3] — 2026-07-10

### Fixed
- CI/publish gate: two more real test deps (psycopg for generated-project boot
  tests, pytest-django for the generated harness run) — verified against a
  clean venv this time, full suite green. 0.9.2 never reached PyPI.

## [0.9.2] — 2026-07-10

### Fixed
- CI/publish install stapel-core from git main: the generated templates depend
  on prodguard/SecretProvider not yet in a PyPI core release (templates
  themselves install core from git). 0.9.1 never reached PyPI (gate failure).

## [0.9.1] — 2026-07-10

### Fixed
- CI/publish workflows install the real test deps (django, DRF, stapel-core) —
  the 0.9.0 publish gate failed at test collection on a bare `pip install pytest`.

## [0.9.0] — 2026-07-09

### Added — release-management R-1: migration-lint + release.json manifest (release-management.md §1/§3/§8)
The OSS mechanism layer of release management (platform models/UI are R-2,
private):
- **`stapel-migration-lint`** (`stapel_tools/migration_lint.py`) — AST-based
  expand/contract gate over Django migration files (no Django boot needed, so
  it runs on customer checkouts at cut time AND on stapel-* module repos in
  CI). Rules: MIG001 destructive op (RemoveField/DeleteModel/Rename*/narrowing
  AlterField) requires `# stapel: contract-phase`; MIG002 `--base-sha`
  verifies the previous release no longer references the destroyed target
  (grep-level via `git show`, new-since-base migrations only); MIG003
  RunPython/RunSQL without reverse requires `# stapel: irreversible` (lowers
  the app's reversible_floor); MIG004 NOT NULL AddField without
  default/db_default on an existing model (breaks N-1); MIG101/MIG102
  warnings. `--json`, `--strict`, exit 1 on errors.
- **`stapel-release-manifest`** (`stapel_tools/release.py`) — builds the open
  `release.json` (schema_version 1): release r\<N\>, git_sha (verified against
  HEAD), images, per-app migration watermarks (max migration FILE at the sha —
  describes the artifact, not a DB), reversible_floor (shared analyzer, latest
  irreversible migration or "zero"), contracts (stapel-* pins: vendored
  checkout pyproject > ==pin > git tag > spec verbatim), config_digest
  (sha256 over `STAPEL_<MOD>` settings blocks), gates (migration_lint
  computed, prodguard/handover_scan recorded via `--gate`), created_at
  (SOURCE_DATE_EPOCH-aware). Byte-deterministic output (sorted keys) — the
  codegen drift-gate discipline.
- **Minimal scaffold Makefile** grows `migration-lint` and `release-manifest`
  targets (the seam the R-2 bake step calls); generated `.gitignore` excludes
  `release.json`. Container bake itself is R-2, deliberately not built.
- Tests: 65 new (every rule incl. a throwaway-git-repo base-sha fixture, floor
  computation, manifest determinism, scaffolded-minimal end-to-end).

### Added — contract artifact freshness gate in release-manifest (process-gap §26)
Caught in production: the stapel-calendar 0.2.3 and stapel-recordings 0.1.3
release bumps raised `version` in `pyproject.toml` but did not regenerate
`docs/capabilities.json` — the tag went out with a stale version baked into
the artifact and the contract tests red. `stapel_tools.release` now catches
this BEFORE the tag:
- **`check_contract_freshness()`** — compares each `docs/*.json` contract
  artifact's own embedded TOP-LEVEL `version` against the repo's
  `pyproject.toml`. REL001 (error): an artifact's version is behind
  pyproject — `build_manifest`/the CLI aborts, no manifest is emitted.
  REL002 (warning): `docs/capabilities.json` is missing while the repo has a
  `make contract` Makefile target — printed, non-fatal. Only
  `capabilities.json` is ever actually flagged by REL001: `schema.json`'s
  OpenAPI version lives nested under `info` (a drf-spectacular placeholder,
  never wired to the module version) and `flows.json`/`errors.json` are bare
  lists with no envelope — looking only at the top level correctly skips
  both without special-casing filenames.
- Unlike `gates.migration_lint` (recorded, not fatal — the pipeline is the
  actual gate), this check is fatal to the manifest build itself.
- Tests: 9 new (clean match, stale capabilities.json, schema.json's nested
  placeholder never checked, missing artifact with/without a contract
  target, no pyproject.toml, `build_manifest` abort/pass-through).

### Added — codegen emits the Gherkin feature bundles (flow-system.md §3)
`stapel_tools.codegen.generate()` now also runs stapel-core's
`generate_flow_features` into `<out>/features/`: one bundle per project
language (localized `.feature` files + the playwright-bdd step library over
the codegen typed client), byte-stable like the three JSON artifacts — the
same drift-gate discipline. New `emit_features()`; the CLI summary reports
the feature-file count. Tests: bundle layout per language, byte-stability,
`generate()` summary.

## [0.8.3] — 2026-07-06

### Changed — generators write the service-navigation registry as env-JSON (admin-suite AS-4)
The service list feeding the admin/Swagger "Services" menu moved out of
framework code (`stapel_core.core.config.STAPEL_SERVICES`, now removed) into a
deploy-config env-JSON. The generators own it:
- **`stapel-create-project`** seeds `STAPEL_SERVICES` in the project `.env` /
  `.env.example`: a monolith gets its single service
  (`[{"name": "<Title>", "prefix": "<slug>"}]`, "All Services" collapses); a
  microservices project gets an empty `STAPEL_SERVICES=[]` for `new-service`
  to fill.
- **`stapel-new-service`** now appends `{"name", "prefix"}` to that env-JSON
  (idempotent by prefix) — the same discipline as `STAPEL_BUS_ROUTES` —
  instead of patching a project-owned `config.py`'s `STAPEL_SERVICES` list
  (the old, largely-dormant behavior, removed).
- Tests: `TestStapelServicesEnv` (monolith seed, microservices empty-then-
  append, idempotent re-registration).

## [0.8.2] — 2026-07-06

### Fixed — three scaffold defects (generated output was dishonest / uncollectable)
- **`stapel-new-service` / `stapel-new-module` app-label collision.** A service
  or module named after a hosted Stapel app (`auth`, `profiles`, `notifications`,
  …) took the bare app label and clashed with `django.contrib.auth`
  (label `auth`) or the hosted `stapel_<x>` module (which sets `label="<x>"`),
  so `django.setup()` raised `ImproperlyConfigured: Application labels aren't
  unique` and **no test could even collect**. The scaffolded `AppConfig` now
  carries an explicit, collision-proof `label = "<module>_local"` (keeps the
  Python `name`; the `_local` suffix marks it the service's OWN app vs. the
  hosted module, and mirrors `core.settings.local`). Safe for existing users:
  the templates never shipped an explicit label and a fresh scaffold has no
  models/migrations, so the label has no `db_table` history to migrate.
- **`stapel-new-react-lib` dishonest `data-analytics="flow"` marker.** The demo
  `DemoButton` hardcoded `data-analytics="flow"` even for a pair with zero flow
  machines — a lie (the button steps no auto-instrumented flow). The scaffold now
  reads the unified `flows.json` (the same source `gen:flows` reads) and picks
  the marker HONESTLY from the module's flow count: `data-analytics="flow"` when
  it owns flows, else `data-analytics="none" data-analytics-reason="no-flow-machines"`.
- **`stapel-new-react-lib` `@stapel/core` peer floor too low.** The floor was the
  monorepo core's current minor, which could sit below `0.3.0` — the minor that
  first re-exported the `createFlowMachine`/`useFlow` primitive every pair
  re-exports. The floor is now `max(0.3.0, current-minor)`, so a pair can never
  advertise a core range (`>=0.2.0`) that lacks the symbol it imports.

## [0.8.1] — 2026-07-06

### Fixed
- `stapel-new-service` / `stapel-create-project` (monolith & microservices)
  generated `LOGIN_REDIRECT_URL = "/{{SLUG}}/admin/"` — a root-relative path
  that 404s once the service is mounted under a prefix. Now emits the URL
  *name* `"admin:index"` (house convention, stapel-core MODULE.md → "URL
  mounting"), same as the example-monolith etalon (`ca64fa7`).
- The scaffolded `AUTH_SERVICE_PREFIX` setting didn't match the name
  `stapel_core.django.mounts` / `AdminLoginRedirectMiddleware` actually read
  (`STAPEL_AUTH_SERVICE_PREFIX`) — every generated service silently had two
  disconnected "is there a dedicated auth service" toggles. Renamed to the
  canonical `STAPEL_AUTH_SERVICE_PREFIX` in both `core/urls.py` and
  `core/settings/base.py` templates. Added `TestMountConventions` regression
  coverage in `tests/test_create_project.py`.


## [0.8.0] — 2026-07-06

### Changed — `stapel-new-react-lib` re-etalon (auth-react after `ebc8f6c`/`4524a53`/`2b1449f`/`8f6b999`)
The React-pair scaffold predated the G1–G8 guardrails contract; this brings it
back to the confirmed etalon. Eight deltas closed:

1. **Typed-event registry** — the pair is wired into `gen:events` /
   `gen:events:check` (root), and `src/analytics/generated/events.json` is a
   generated, drift-gated surface (documented in README/MODULE.md).
2. **Demo layer** — `demo/_harness.tsx` (mock-`fetch`, token chrome via
   `cssVar()`, `demo.*` i18n keys, `data-analytics="flow"`, `run` prop) plus a
   starter `<Camel>.demo.tsx` that covers the starter headless export
   (`<Camel>Provider`), so the `gen:demos` completeness gate passes on a fresh
   scaffold; `tsconfig.demo.json` compiles demos as first-class code.
3. **`@stapel/showcase`** (and `@stapel/tokens`) added as **devDependencies**
   only — never runtime/peer.
4. **`manifest.backend.contract`** — `gen:manifest` wired with
   `MANIFEST_BACKEND_PYPROJECT` so the manifest states the backend semver range
   it was generated against (a backend minor bump reddens the gate).
5. **Etalon test family** — `demos.test.tsx` (glob smoke-render),
   `prodBundlePurity.test.ts` (real `npm pack --dry-run` ground truth),
   `errorsBundle.test.ts` (en-fallback coverage), `flowsContract.test.ts`
   (registry integrity) — replacing the single `pair.test.ts` (a slim residual
   retains query-key + drift-gated manifest self-description).
6. **Peer policy** — `@stapel/core` peer is a pinned floor
   `>=<current-core-minor>.0 <1.0.0` (read from the monorepo core package.json at
   scaffold time), not `workspace:^` — stops changesets force-majoring the pair
   on an out-of-range core minor. The local link stays `workspace:^` (devDep).
7. **Root `gen`/`gen:check` enumeration** — matching the etalon (pairs own NO
   `gen:*` scripts; the drivers live at the root and are listed per package), the
   scaffold now **idempotently patches** the monorepo root package.json,
   appending one env-parametrized invocation per driver
   (flows/errors/events/demos/manifest) to each `gen:*` and `gen:*:check`. Falls
   back to printing the exact edits when the root shape is unexpected.
8. **CSS guardrail** — README documents `lint:css`/stylelint alongside the
   ESLint plugin.

Fork-free preserved: the scaffold WIRES the etalon's env-parametrized
`scripts/gen-*.mjs` drivers (via `FLOW_MODULE`/`ERRORS_*`/`EVENTS_PKG_DIR`/
`DEMOS_PKG_DIR`/`MANIFEST_*`), never copies driver logic. Also fixed a latent
pre-contract bug: the old per-pair `gen:errors` used a nonexistent env knob
(`AUTH_ERRORS_SOURCES`) pointed at `errors.py`; the driver reads
`AUTH_ERRORS_JSON` (a backend `docs/errors.json`).

Smoke-validated end-to-end on a throwaway `notifications-react` in stapel-react
(install + all 5 gens + build + lint + lint:css + test 14/14 + size 720 B; the
completeness gate fails closed when the starter demo is removed).

## [0.7.0] — 2026-07-06

### Added — i18n doc-link lint rule + seed export (i18n-shipping wave 0)
- **`stapel-lint` R100** (WARNING) — when a repo carries i18n artifacts, its
  README must link the docs in *each* language (i18n-shipping.md §4): if
  `docs/flows/` exists, a link per flow-doc language (en + ru at minimum); if
  `docs/errors.json` or any `docs/errors.<lang>.md` exists, a link per error
  language. Emitted at warning level — the convention is rolling out (W→E after
  the sweep), so `stapel-lint` now exits non-zero only on *error*-level
  violations; warnings are printed but non-blocking. `Violation` gained a
  `level` field (`"error"` default). Repo-level checks (README ↔ artifacts) run
  once per directory root, alongside the existing per-file AST rules.
- **`stapel-i18n-seed`** — one-shot export of a `translate_catalogs` seed from
  the curated `stapel-translate` builtin fixtures
  (`fixtures/builtin/<lang>.json`): `--fixtures DIR --domain {errors,notifications}
  --lang X [--out FILE]` projects the flat corpus, filtered to the domain's key
  prefix, into a byte-stable seed file (sorted keys, matches
  `stapel_core.i18n.dump_catalog`). This is how the first ru of a module's
  errors is *copied* from the paid-for corpus rather than re-translated
  (i18n-shipping.md §5, requirement "clients don't spend tokens").

## [0.6.1] — 2026-07-06

### Fixed
- `stapel-codegen`'s `generate()` now also emits `errors.json` alongside
  `schema.json`/`flows.json`, wired onto stapel-core's `generate_error_keys`
  management command (the mechanism landed in stapel-core `08b6c40` but was
  never plumbed into the orchestrator). Same byte-stable re-normalisation and
  drift-gate invariant as the other two artifacts; format matches
  stapel-auth's `docs/errors.json` (`code`/`status`/`params`/`remediation`/`en`).

## [0.6.0] — 2026-07-06

### Added — `stapel-analytics-report` (frontend-guardrails §3.3, task G5)
- New CLI `stapel-analytics-report <workspace-dir>` (+ `--package DIR`,
  repeatable, for a single package/app). Generates the typed-analytics summary
  report across a pnpm workspace of `@stapel/*-react` pairs and/or a customer
  app, from STATIC generated artifacts only (§3.3): `events.json` /
  `manifest.events` (defineEvent catalog + auto-instrumented flow funnels),
  `flows.json` (canonical backend flows, prose + endpoints), `manifest.machines`,
  and a syntactic scan of TS/TSX for call sites (`tracked()`/`trackedSubmit()`/
  `track()` emit points, `data-analytics="flow"/"none"` markers with reasons,
  `eslint-disable … -- description` escape hatches).
- Two always-separate slices — **app** (customer code) and **library**
  (`@stapel/*` pairs) — reported summarily and split, classified by package name.
- Outputs: machine-readable `report.json` (for the Studio project passport,
  user decision Q13) plus presentable human-readable `report.md` and self-
  contained (CSP-safe, theme-aware) `report.html`. `--out DIR` writes all three;
  otherwise `--format {json,md,html,all}` prints to stdout.
- Per event: description, typed props (types + options + descriptions), emit
  sites (`file:line` + enclosing component), and the linked backend flow when
  declared. Flow funnels list their documented steps. The canonical flow report
  joins backend `flows.json` (prose title/description, actors, endpoints) with
  frontend coverage (covering pairs, funnel event, name-matched machine, linked
  app events) and renders a `[gated: <ENV>]` badge from the `gated_by` field
  (placeholder for task G6 — absent field means always-on). Coverage summary
  counts clickable outcomes by static marker (tracked / flow / untracked /
  disabled).
- Cross-file (and `import { X as Y }` alias) call-site → event resolution reuses
  the events.json TS-AST catalog (produced by `scripts/events-lib.mjs`) as the
  authoritative event set — not the intentionally conservative in-file lint
  resolver. Missing `events.json` degrades to source-derived bindings and the
  `manifest.events` fallback; a package with no catalog at all is flagged, never
  crashes. `--capabilities` is reserved for the §3.4 env-aware mode (ignored).
- Pure Python, zero new dependencies (no Node runtime): the heavy TS-AST work is
  already done by the workspace's drift-gated `gen:events` and consumed here.

## [0.5.0]

### Added
- `stapel-new-react-lib <module>` — scaffold a headless `@stapel/<module>-react`
  pair into a stapel-react monorepo, from the auth-react etalon (frontend-standard
  §9, frontend-core-architecture §4 checklist). Emits the full layer stack
  (`api → model → flows → headless → i18n`), namespaced query keys, the
  `create<Module>Runtime`/`<Module>Provider` wiring, the module-scoped
  `toFlowError`/i18n bundle, an errors map with `explain<Module>Error`, a vitest
  smoke suite, and package hygiene (ESM, `sideEffects:false`, `isolatedDeclarations`,
  src-in-tarball, size-limit, exports for `manifest`/`llms.txt`). The
  `createFlowMachine` primitive is IMPORTED from `@stapel/core`, never copied.
  Fork-free: the generated `package.json` wires the etalon's env-parametrized
  monorepo drivers (`scripts/gen-{flows,errors,manifest}.mjs`) via env knobs
  (`FLOW_MODULE`, `ERRORS_*`, `MANIFEST_*`) rather than duplicating codegen — a
  pair owns three per-package drift gates (`gen:{flows,errors,manifest}:check`);
  `gen:api` stays core-owned. Usage: `stapel-new-react-lib notifications
  [--backend stapel-notifications] [--path-prefix /notifications/api/]
  [--react-dir <stapel-react>]`.

## [0.5.1] — 2026-07-06

### Added — settings hardening + generated secrets (SEC-4/SEC-6)
- **prod settings tier (monolith/microservices `core/settings/prod.py`):**
  `SECURE_SSL_REDIRECT=True`, a conservative `SECURE_HSTS_SECONDS=86400`
  (no `include_subdomains`, no `preload` — both one-way doors, left for the
  deploying team to decide; ramp to `31536000` once HTTPS is verified
  stable), `SECURE_CONTENT_TYPE_NOSNIFF=True`, and `JWT_COOKIE_SECURE=True`
  alongside the existing `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`
  (security-programme.md gaps B1/B3). Also ships a report-only
  Content-Security-Policy (Django's native CSP middleware, Django>=6; a
  `default-src 'self'`-based policy) — report-only because a strict enforced
  policy can break django-admin/Vite inline scripts without per-project
  tuning (gap B7, §8.4 open question); the CSP block is skipped gracefully
  (`try`/`except ImportError`) on Django<6.
- **`stapel_core.django.prodguard` (new in stapel-core 0.8.1-unreleased)**
  wired into prod settings: `guard_secret` rejects an empty, `change_me*`-
  or `django-insecure-`-prefixed, or under-50-character `SECRET_KEY`/
  `JWT_SECRET_KEY`; `guard_db_password` rejects the shipped
  `POSTGRES_PASSWORD` placeholder/dev-default (`change_me`/`stapel`). The
  previous inline guard only caught an empty or `django-insecure-`-prefixed
  `SECRET_KEY` — the actual `.env.example` placeholder
  (`change_me_to_a_long_random_string`) sailed straight through (gap B2/B6).
- **Minimal preset now has a prod profile** (previously none at all, gap
  B8): `core/settings.py` gains a `DJANGO_ENV` switch (default `local`,
  unaffected DX — `DEBUG`/`ALLOWED_HOSTS` behave exactly as before) and a
  `DJANGO_ENV=prod` branch applying the same `SECURE_*`/HSTS/CSP-report-only
  hardening and `guard_secret` check as the monolith/microservices tiers.
  `.env.example`/README now carry a "NOT FOR PRODUCTION by default" banner.
  The hardcoded insecure `SECRET_KEY` fallback is gone — it only applies
  outside `DJANGO_ENV=prod`, same shape as the monolith/microservices dev
  fallback.
- **`stapel-create-project` now generates `.env` (with a fresh random
  `SECRET_KEY`) for the minimal preset too** (SEC-6) — previously only
  monolith/microservices got generated secrets; minimal fell through to the
  hardcoded dev-only fallback with no `.env` at all. `.env.example` keeps
  the placeholder (safe to commit); `.env` is gitignored, as before.
- Fixed the post-generation guidance for monolith/microservices: it used to
  print `cp .env.example .env  # fill in secrets`, which — followed
  literally — would overwrite the already-generated random secrets with the
  committed placeholders right back. Now states `.env` was already created
  with generated secrets.

### Changed
- `stapel-new-library` artifact hygiene (top-tier packaging): the generated
  `tests` package is no longer listed in `[tool.setuptools] packages`, so test
  files and `conftest.py` no longer ship inside the built wheel/sdist (the
  flat-layout editable install still resolves `<pkg>.tests.urls` for the test
  `ROOT_URLCONF`, so the scaffold suite stays green). Generated `pyproject.toml`
  now carries a full `[project.urls]` block, completed trove classifiers
  (`License :: OSI Approved :: MIT License`, Python 3.13, `Typing :: Typed`,
  `Intended Audience`, `Operating System`, `Development Status`,
  `Python :: 3 :: Only`) matching the CI matrix, and a `[tool.ruff]` section
  single-sourcing the lint config the git hooks/CI pass on the CLI. Generated
  `.gitignore` now also covers `.ruff_cache/`, `.mypy_cache/`, `coverage.xml`,
  `junit.xml`, `.DS_Store` and `*.err`.
- Dropped an unused `pytest` import in `tests/test_codegen.py` (ruff F401).

### Fixed
- `stapel-create-project --modules <mod>` on **minimal** projects now wires each
  chosen module into `INSTALLED_APPS` and mounts its urls under `/<mod>/api/`,
  not just into `requirements.txt` (G10). A module installed but absent from
  `INSTALLED_APPS` was dead weight; url includes mirror how
  stapel-example-monolith mounts per-module urls. (Monolith already wired
  modules via the service scaffold — covered by new regression tests.)
- Generated **minimal** `requirements.txt` now pins framework ranges
  (`django>=6,<7`, `djangorestframework>=3.14,<4`) — the Django line every
  stapel suite is actually validated on (the source codebases and the
  workspace venv run Django 6; 5.1/5.2 has never been tested) — instead of
  the stale `django>=4.2,<5.0` floor that let a fresh project ride an
  untested Django (G11, version skew). Note: stapel-core still declares
  `Django>=5.1`, an untested claim tracked in
  docs/module-extension-gaps.md.

## 0.3.1 — 2026-07-04
### Added
- `stapel-new-library` — scaffolds a standalone `stapel-*` package repo
  implementing the library standard (workspace `docs/library-standard.md`):
  flat-layout packaging, `STAPEL_<NAME>` conf namespace, comm surface with
  JSON schemas (ping example), serializer seams, MODULE.md skeleton,
  community files, codecov ratchet/floor policy, CI/publish workflows,
  ruff git hooks. Two kinds: `module` (service-capable Django app) and
  `library` (importable L1 package). Generated repo's own tests pass
  out of the box.

## 0.3.0 — 2026-07-03

### Added
- Generator test suite; template/generator refinements.

### Fixed
- Committed bytecode removed from tracking and ignored.


## 0.2.0 — 2026-07-02

- (see git log — changelog discipline starts here; add entries with each PR)
