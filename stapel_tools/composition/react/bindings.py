"""The React bindings of the brick registry — one ``ReactBinding`` per pair.

``FRONTEND_REACT_LIBS`` (create_project) is computed from these and from the
neutral ``composition.bricks.BRICK_UI``; see ``composition.react.registry``.
A pair's nav entries and embeds are declared ONCE, neutrally, in
``bricks.py``; this file names only how React delivers them (package,
version, providers, and the component each nav id / embed id renders with).
"""
from __future__ import annotations

from .binding import ReactBinding

# Frontend wiring registry — the published `@stapel/<key>-react` headless
# pairs (stapel-react/packages/*-react), keyed by the SAME STAPEL_LIBS key so
# a project's `--modules` selection drives BOTH sides by construction. Only
# modules with an actual published pair are listed here; any other selected
# lib (e.g. "currencies", "tasks") scaffolds a plain frontend with zero
# wiring — see the React emitter (`composition/react/emitter.py`).
#
# ONE key here has no STAPEL_LIBS entry: "search". `@stapel/search-react` is
# published and carries a nav manifest, but stapel-search is not in this
# file's backend registry, so a monolith cannot select it and the filter in
# `_frontend_react_entries` never sees it. It is listed anyway because this
# dict is ALSO the pair registry `stapel-frontend-repo-init --surface public`
# reads (a storefront names its pairs directly, not through a lib selection),
# and because the nav mirror below is what the drift gate checks. Splitting it
# into a second registry would mean two places to pin one package.
#
# `embeds` is the same kind of MANUALLY PINNED MIRROR, of the `embeds` key of
# that pair's `nav-manifest.json` (composition spec §3): the chrome-shaped
# components a pair offers to a shell slot. Gated by the same script; a pair
# that offers none carries no key, exactly as its manifest does.
#
# 2026-10-22 (stapel-tools 0.72.9): the slot-embeds train — `npm view
# @stapel/<key>-react version` -> auth 0.27.0 (`auth.account_menu`),
# workspaces 0.22.1 (`workspaces.switcher`, `shell_provider`; 0.22.1 is the
# first to ship its nav-manifest.json), cdn 0.8.2, listings 0.41.0 and
# categories 0.36.0 (the docs/llms and stable-enum-name trains of the same day).
#
# 2026-10-15 (stapel-tools 0.72.0): the composition train — `npm view
# @stapel/<key>-react version` -> notifications 0.13.0 and translate 0.3.0
# (the minors that publish `embeds`), workspaces 0.21.0, auth 0.26.0.
#
# Pins re-verified 2026-09-14 (stapel-tools 0.67.2) against `npm view
# @stapel/<key>-react version --prefer-online` for every entry, clearing the
# whole backlog the `--registry` gate had been naming on every daily run:
# nineteen pins moved, four of them by more than twenty minors
# (search 0.15.0 -> 0.48.1, listings 0.14.1 -> 0.35.0,
# categories 0.9.1 -> 0.31.4, chat 0.6.1 -> 0.19.1). Not one nav MIRROR
# changed — `check_nav_manifest_sync.py` had been reporting every one of them
# as "entries are identical, only the version differs", which is precisely the
# drift a reader diffing this file cannot see and the reason the gate asks npm.
# The five pairs that publish no nav manifest (attributes, cdn, currencies,
# reviews, vocabularies) were stale too and are raised in the same pass: the
# --registry gate walks nav-bearing pairs only, so nothing was naming them,
# and `@stapel/chat-react` 0.19.1 peers `@stapel/cdn-react ">=0.5.0"` while
# `@stapel/categories-react` 0.31.4 peers `@stapel/attributes-react ">=0.16.6"`
# — two pins this table would otherwise have installed BELOW a pair's floor.
#
# Earlier pins were verified 2026-08-31 against BOTH the sibling stapel-react
# checkout's `packages/<key>-react/nav-manifest.json` `version` AND the live
# `npm view @stapel/<key>-react version` (identical for every entry — the
# workspace checkout is not ahead of its own last publish for these
# packages). The attributes-v2 wave moved seven of them at once
# (auth 0.18.1, attributes 0.5.0, categories 0.5.1, chat 0.6.1,
# listings 0.9.1, reviews 0.6.0, search 0.9.1) and NONE of their nav
# surfaces changed — only the pins did, which is exactly the drift
# `scripts/check_nav_manifest_sync.py` exists to catch, since a version
# mismatch is invisible to a reader diffing entries.
# `@stapel/core`/`@tanstack/react-query`/`antd`/
# `@stapel/tokens-antd` pins come from the live npm registry (the ACTUAL
# resolution a fresh `npm install` gets), not the sibling checkout's
# workspace-protocol devDependency pins (those can trail npm — e.g.
# stapel-react's own local `@stapel/core` was 0.6.1 while npm already had
# 0.6.2 at pin time).
#
# Every pair's `create<Module>Runtime` takes the SAME generic
# `CreateModuleRuntimeOptions` shape from `@stapel/core` (just `baseUrl` —
# confirmed by reading each pair's `src/model/runtime.ts`; chat/listings/
# reviews/cdn add OPTIONAL fields on top, and the scaffold leaves them unset
# except where a flag says otherwise) EXCEPT auth's, which accepts extra
# optional session-lifecycle callbacks the generated scaffold deliberately
# leaves unset (no invented routes) — so the `baseUrl: "/<key>/api/v1/"` call
# shape below is uniform, never module-specific codegen.
#
# `attributes` is the one L0 pair: `@stapel/attributes-react` has no client,
# no queries and no backend HTTP surface at all (stapel-attributes is a
# library, not a service), so it carries NO `provider`/`create_runtime` — only
# `register_i18n`. `render_modules_tsx` reads the absence rather than a flag.
#
# `default_component` names the ONE headless pair's `./default` (antd skin)
# export that is genuinely mountable with **zero required props** (read off
# each pair's own `src/default/*.tsx` prop interfaces — the actual gate, not
# a guess): `AuthPanel` (auth), `NotificationFeedList` (notifications),
# `ProfileSettings` (profiles). Pairs whose `/default` screens are all
# WORKSPACE screens (`workspaces`, `forms`) are wired provider-only even
# though their props are optional now: those screens read the runtime's
# active workspace selection, which their own nav contract establishes by
# routing them — a bare mount outside it renders "choose a workspace" and
# nothing else. `billing`, `calendar` and `recordings` are provider-only for
# the same reason and not for the older one (they DO ship a `/default`
# subpath and an antd peer dep): each publishes a nav manifest, so its
# screens arrive as ROUTES, and a second bare copy dropped on the panel
# would be the same screen twice. Absent key = no default component to mount.
#
# `nav` is a MANUALLY PINNED MIRROR of that pair's own `nav-manifest.json`
# (the same discipline as the version pins). Drift between mirror and real
# file is caught by `scripts/check_nav_manifest_sync.py` — wired into
# `make check` and CI, never left to a reader. WHICH nav entries a generator
# can actually MOUNT is a separate question, answered once in
# `_frontend_templates.NAV_ENTRY_MOUNTS`; an entry whose component needs a
# prop generation cannot fabricate gets a placeholder page that NAMES the
# absence, never a broken mount and never silence.
REACT_BINDINGS: dict[str, ReactBinding] = {
    # auth.login is `menuVisibleDefault: false` (a sign-in screen is never
    # a menu tab); auth.qr_confirm declares `surface: "public"` while
    # ALSO requiring auth — the fleet's first entry proving the two axes
    # are independent (a signed-in phone confirms a signed-out desktop);
    # auth.security nests under profiles.settings (dropped by
    # resolveNav's own orphan rule when profiles isn't installed). The
    # five `admin.*` entries hang from the container-owned `admin.root`,
    # so an install without an admin section drops them the same way.
    #
    # The account control at the trailing edge of the top bar. Member-only;
    # it opens the container-owned account section when the project routes
    # one, and offers sign-out alone when it does not.
    "auth": ReactBinding(
        package="@stapel/auth-react",
        version="0.28.1",
        provider="AuthProvider",
        create_runtime="createAuthRuntime",
        register_i18n="registerAuthI18n",
        default_component="AuthPanel",
        components={
            "auth.login": ("AuthPanel", "default"),
            "auth.qr_confirm": ("QrConfirmPanel", "default"),
            "auth.security": ("SecuritySettings", "default"),
            "admin.sso_orgs": ("SsoOrgsPanel", "default/admin"),
            "admin.service_keys": ("ServiceKeysPanel", "default/admin"),
            "admin.staff_roles": ("StaffRolesPanel", "default/admin"),
            "admin.users_create": ("AdminUsersPanel", "default/admin"),
            "admin.auth_audit": ("AdminAuditPanel", "default/admin"),
        },
        embed_components={
            "auth.account_menu": ("AccountMenu", "default"),
        },
    ),
    # L0 — no client, no provider, no runtime. See the dict docstring.
    "attributes": ReactBinding(
        package="@stapel/attributes-react",
        version="0.18.0",
        register_i18n="registerAttributesI18n",
    ),
    # One entry, hanging from the container-owned `account.root`: the
    # wallet is an account screen, never a tab of its own.
    "billing": ReactBinding(
        package="@stapel/billing-react",
        version="0.12.0",
        provider="BillingProvider",
        create_runtime="createBillingRuntime",
        register_i18n="registerBillingI18n",
        components={
            "account.billing": ("WalletPanel", "default"),
        },
    ),
    # `calendar.availability` nests under `calendar.month`, a parent this
    # SAME pair declares — so both stand or fall together, and no
    # container root is involved.
    "calendar": ReactBinding(
        package="@stapel/calendar-react",
        version="0.9.1",
        provider="CalendarProvider",
        create_runtime="createCalendarRuntime",
        register_i18n="registerCalendarI18n",
        components={
            "calendar.month": ("Calendar", "default"),
            "calendar.availability": ("AvailabilityPane", "default"),
        },
    ),
    "categories": ReactBinding(
        package="@stapel/categories-react",
        version="0.36.0",
        provider="CategoriesProvider",
        create_runtime="createCategoriesRuntime",
        register_i18n="registerCategoriesI18n",
        components={
            "categories.catalog": ("CatalogPage", "default"),
            "categories.category": ("CategoryPage", "default"),
        },
    ),
    # No nav manifest, deliberately: the uploader is drawn INSIDE another
    # pair's screen (listing composer, profile settings), so a "Uploads"
    # menu item leading nowhere would be worse than its absence
    # (storefront spec §13.6 item 8).
    "cdn": ReactBinding(
        package="@stapel/cdn-react",
        version="0.8.2",
        provider="CdnProvider",
        create_runtime="createCdnRuntime",
        register_i18n="registerCdnI18n",
    ),
    "chat": ReactBinding(
        package="@stapel/chat-react",
        version="0.23.1",
        provider="ChatProvider",
        create_runtime="createChatRuntime",
        register_i18n="registerChatI18n",
        components={
            "chat.conversations": ("ConversationListPanel", "default"),
        },
    ),
    # No `nav` mirror, and none is missing: `@stapel/currencies-react`
    # 0.3.0 publishes a `nav-manifest.json` whose `entries` array is
    # EMPTY (verified against the sibling checkout — the gate's "claims
    # nothing, publishes nothing" case). Money is drawn inside somebody
    # else's screen: a price on a listing card, a total in a wallet, a
    # picker in the composer. A "Currencies" menu row leading nowhere
    # would be worse than its absence.
    #
    # No `default_component`: every `/default` export needs props a
    # scaffold cannot fabricate — `Price` an amount, `CurrencyField` a
    # value, `CurrencyPicker` `value`/`onChange`/`options`, `RateTable`
    # `rates`/`base` (read off the pair's own `src/default/*.tsx`).
    #
    # The pair's api layer spells `api/v1/` ITSELF
    # (`api/currenciesApi.ts`: `CURRENCIES_LIST_PATH = "api/v1/"`), so its
    # `baseUrl` ends at the MOUNT. The registry's uniform
    # `/<key>/api/v1/` would send every catalogue read to
    # `/currencies/api/v1/api/v1/` — a 404 on a screen that looks wired.
    "currencies": ReactBinding(
        package="@stapel/currencies-react",
        version="0.4.0",
        provider="CurrenciesProvider",
        create_runtime="createCurrenciesRuntime",
        register_i18n="registerCurrenciesI18n",
        base_url="/currencies/",
    ),
    # The backend has been selectable since the forms module landed
    # (STAPEL_LIBS "forms"), and the pair was published — but this registry
    # had no entry, so `stapel-create-project --modules forms` shipped a
    # headless backend and a frontend that could not see it (forms-react
    # audit FR-3). Registered here, its runtime, provider and catalogue join
    # `modules.tsx` like every other pair's.
    #
    # No `default_component`: `FormsListPane` mounts with zero required
    # props, but it is a workspace screen that reads the runtime's active
    # selection — the pair's own nav contract routes it rather than
    # dropping it on a page — so the scaffold routes it too, or not at all.
    #
    # The `nav` mirror below is what makes those screens ROUTABLE: the
    # builder and the responses table both read `:formId` off the address
    # (NAV_ENTRY_MOUNTS route_params), and all three hang from the
    # container-owned `account.root`.
    "forms": ReactBinding(
        package="@stapel/forms-react",
        version="0.4.2",
        provider="FormsProvider",
        create_runtime="createFormsRuntime",
        register_i18n="registerFormsI18n",
        components={
            "forms.list": ("FormsListPane", "default"),
            "forms.builder": ("FormBuilderPane", "default"),
            "forms.responses": ("ResponsesPane", "default"),
        },
    ),
    "gdpr": ReactBinding(
        package="@stapel/gdpr-react",
        version="0.4.1",
        provider="GdprProvider",
        create_runtime="createGdprRuntime",
        register_i18n="registerGdprI18n",
        components={
            "account.privacy": ("PrivacyPane", "default"),
            "public.privacy-request": ("PrivacyRequestPane", "default"),
            "admin.privacy": ("PrivacyAdminPane", "default/admin"),
        },
    ),
    "translate": ReactBinding(
        package="@stapel/translate-react",
        version="0.3.0",
        provider="TranslateProvider",
        create_runtime="createTranslateRuntime",
        register_i18n="registerTranslateI18n",
        components={
            "account.language": ("LanguageSettingsPane", "default"),
        },
        embed_components={
            "translate.language_switcher": ("LanguageSwitcher", "default"),
        },
    ),
    # No `nav` mirror, and none is missing: `@stapel/geo-react` publishes
    # NO `nav-manifest.json` at all (verified against the sibling
    # checkout). The whole pair is one FIELD drawn inside somebody else's
    # form — the listing composer's `locationPicker` slot — exactly as
    # cdn's uploader and vocabularies' term select are.
    #
    # No `default_component`: `/default` ships `LocationField`, whose
    # every prop is optional but which is meaningless outside a form that
    # reads its answer. It reaches a container through the composite
    # seam below, never as a panel tile.
    #
    # The mount, not the versioned prefix: `api/geoApi.ts` spells
    # `MAP_CONFIG_PATH = "api/v1/map/config"` itself, and the other four
    # paths are HANDED to it by that call's `endpoints` table. The
    # uniform `/geo/api/v1/` would make the bootstrap read
    # `/geo/api/v1/api/v1/map/config` and the picker would never open.
    "geo": ReactBinding(
        package="@stapel/geo-react",
        version="0.7.1",
        provider="GeoProvider",
        create_runtime="createGeoRuntime",
        register_i18n="registerGeoI18n",
        base_url="/geo/",
    ),
    # 0.43.0: `ListingDetailPane` takes `slots` (`page.actions`).
    #
    # Declared an OPTIONAL peer, but `/default`'s ListingPrice imports it
    # statically, so a bundle without the package fails `vite build`
    # ("formatMoney" is not exported by __vite-optional-peer-dep). The
    # package is installed with the pair; its provider stays opt-in (the
    # skin reads the runtime context as absent).
    #
    # The dock's own label: five destinations across a phone
    # cannot each carry "Post a listing".
    "listings": ReactBinding(
        package="@stapel/listings-react",
        version="0.43.0",
        provider="ListingsProvider",
        create_runtime="createListingsRuntime",
        register_i18n="registerListingsI18n",
        static_peer_pairs=("currencies",),
        components={
            "listings.detail": ("ListingDetailPane", "default"),
            "listings.compose": ("ListingComposerPage", "default"),
            "listings.mine": ("MyListingsPane", "default"),
            "listings.favorites": ("FavoritesPane", "default"),
        },
    ),
    # The pair that closes the loop stapel-classified opens: the backend
    # registers listing/review report targets, and until this entry
    # existed nothing in a generated container rendered a button for one.
    #
    # No `default_component`: all four `/default` screens take zero
    # required props (`PolicyDisclosurePaneProps`, `AppealPanelProps`,
    # `ModerationQueueProps`, `AppealsQueueProps` — every field optional,
    # read off the pair's own `src/default/*.tsx`), so a bare mount would
    # COMPILE. It is still wrong: each of the four is a ROUTE in the nav
    # mirror below, and a second copy dropped on the module panel is the
    # same screen twice — the reason billing/calendar/recordings are
    # provider-only too.
    #
    # `ReportButton`/`ReportSheet` stay unmounted for the opposite
    # reason: they take a `targetType`/`targetKey` that only the screen
    # embedding them knows (a listing card knows it is a listing), so
    # they belong inside another pair's screen and nowhere a generator
    # can put them.
    #
    # Two `admin.*` entries hang from the container-owned `admin.root`
    # and `account.appeals` from `account.root`, so an install with no
    # such section drops them by resolveNav's own orphan rule.
    # `moderation.policy` is the one PUBLIC entry: a disclosure page a
    # signed-out visitor may read (DSA transparency), off the menu.
    "moderation": ReactBinding(
        package="@stapel/moderation-react",
        version="0.4.0",
        provider="ModerationProvider",
        create_runtime="createModerationRuntime",
        register_i18n="registerModerationI18n",
        components={
            "moderation.policy": ("PolicyDisclosurePane", "default"),
            "account.appeals": ("AppealPanel", "default"),
            "admin.moderation": ("ModerationQueue", "default/admin"),
            "admin.moderation-appeals": ("AppealsQueue", "default/admin"),
        },
    ),
    # The bell in the top bar. Member-only (an unread count needs a
    # session) and it opens the feed entry above.
    "notifications": ReactBinding(
        package="@stapel/notifications-react",
        version="0.13.1",
        provider="NotificationsProvider",
        create_runtime="createNotificationsRuntime",
        register_i18n="registerNotificationsI18n",
        default_component="NotificationFeedList",
        components={
            "notifications.feed": ("NotificationsPage", "default"),
            "notifications.push": ("PushSettingsPane", "default"),
        },
        embed_components={
            "notifications.bell": ("NotificationBell", "default"),
        },
    ),
    "profiles": ReactBinding(
        package="@stapel/profiles-react",
        version="0.27.1",
        provider="ProfilesProvider",
        create_runtime="createProfilesRuntime",
        register_i18n="registerProfilesI18n",
        default_component="ProfileSettings",
        components={
            "profiles.settings": ("ProfileSettings", "default"),
            "profiles.language": ("LanguageSettings", "default"),
            "profiles.notifications": ("NotificationPreferences", "default"),
            "profiles.contacts": ("ContactsManager", "default"),
            "profiles.connections": ("ConnectionsPage", "default"),
            "profiles.public": ("PublicProfilePage", "default"),
        },
    ),
    # npm view @stapel/recordings-react version -> 0.11.1 (2026-10-15)
    #
    # `share.view` is the fleet's second public-surface screen carried by
    # an otherwise member-only pair: a share link is opened by someone
    # with no session at all, so `requiresAuth: false` and the token in
    # the address are the whole access decision.
    #
    # RELATIVE to the parent (`recordings.list`), as of the pair's
    # 0.6.2 manifest: the plan builder joins parent + child
    # (`build_app_route_plan`), so the old self-prefixed
    # "recordings/:recordingId" emitted
    # `recordings/recordings/:recordingId` and the detail screen
    # was unreachable in every generated container.
    "recordings": ReactBinding(
        package="@stapel/recordings-react",
        version="0.11.2",
        provider="RecordingsProvider",
        create_runtime="createRecordingsRuntime",
        register_i18n="registerRecordingsI18n",
        components={
            "recordings.list": ("RecordingsList", "default"),
            "recordings.detail": ("RecordingDetailPane", "default"),
            "share.view": ("SharedRecordingView", "default"),
        },
    ),
    # No nav manifest either — reviews render inside a listing page and a
    # seller page, never on a route of their own (§13.8 item 11).
    "reviews": ReactBinding(
        package="@stapel/reviews-react",
        version="0.9.0",
        provider="ReviewsProvider",
        create_runtime="createReviewsRuntime",
        register_i18n="registerReviewsI18n",
    ),
    # No STAPEL_LIBS entry — see the dict docstring.
    "search": ReactBinding(
        package="@stapel/search-react",
        version="0.52.1",
        provider="SearchProvider",
        create_runtime="createSearchRuntime",
        register_i18n="registerSearchI18n",
        components={
            "search.results": ("SearchPage", "default"),
            "search.ranking": ("RankingDisclosurePane", "default"),
        },
    ),
    "video": ReactBinding(
        package="@stapel/video-react",
        version="0.4.0",
        provider="VideoProvider",
        create_runtime="createVideoRuntime",
        register_i18n="registerVideoI18n",
        components={
            "video.rooms": ("RoomsPane", "default"),
            "admin.usage": ("ScopeUsagePane", "default"),
        },
    ),
    # No `nav` mirror, and none is missing: `@stapel/vocabularies-react`
    # publishes NO `nav-manifest.json` at all (verified against the
    # sibling checkout — the gate's "claims nothing, publishes nothing"
    # case, which is in sync by its own rules). The pair draws a term
    # SELECT inside somebody else's editor — a listing composer's
    # `ref_select` field — exactly as cdn's uploader and reviews' stars
    # do, so a "Vocabularies" menu row leading nowhere would be worse
    # than its absence.
    #
    # No `default_component` for the same reason: `/default` ships
    # `VocabularyTermSelect`, which REQUIRES `vocabulary`/`level`/`value`
    # props a scaffold cannot fabricate.
    #
    # The attributes-v2 §3.4 SEAM, and the first entry in this registry to
    # declare one. `@stapel/attributes-react` DECLARES the
    # `VocabularyClient` interface and reads it through
    # `<VocabularyClientProvider>`; this pair IMPLEMENTS it structurally,
    # and neither package imports the other — two L2 pairs that stay
    # independently releasable, joined by the container. That container is
    # what a generator writes, so the join belongs here rather than in
    # every storefront's hand-written `modules.tsx` (a client fleet's
    # storefront hand-wired exactly this, and nothing here could have
    # caught it).
    #
    # `provider_package` is a pair KEY's package, deliberately: the
    # emission is gated on `attributes` ALSO being selected, because the
    # provider is that package's export. Vocabularies without attributes
    # installs a client nothing reads, which is wiring, not a screen —
    # so the seam is simply not emitted and the runtime still is.
    "vocabularies": ReactBinding(
        package="@stapel/vocabularies-react",
        version="0.5.0",
        provider="VocabulariesProvider",
        create_runtime="createVocabulariesRuntime",
        register_i18n="registerVocabulariesI18n",
        seam={"factory": "createVocabularyClient", "provider": "VocabularyClientProvider", "provider_package": "@stapel/attributes-react", "provider_pair": "attributes", "base_url": "/vocabularies/api/v1/"},
    ),
    # Five account screens plus `workspaces.invite`, which is PUBLIC and
    # absolute (`/invite/:token`): an invitee opens it before they have an
    # account, so it cannot hang under `account.root`. `workspaceId` is
    # optional on all five account screens precisely because this contract
    # ROUTES them — the runtime's active selection is what they read (see
    # the `default_component` note in this dict's docstring).
    #
    # The switcher right after the brand. It and the five account screens
    # above read ONE selection, so the container mounts
    # `shell_provider` around the member shell whenever this pair is in.
    "workspaces": ReactBinding(
        package="@stapel/workspaces-react",
        version="0.23.0",
        provider="WorkspacesProvider",
        create_runtime="createWorkspacesRuntime",
        register_i18n="registerWorkspacesI18n",
        shell_provider="WorkspaceSelectionProvider",
        components={
            "workspaces.list": ("WorkspacesPage", "default"),
            "workspaces.settings": ("WorkspaceSettings", "default"),
            "workspaces.members": ("MembersManager", "default"),
            "workspaces.invitations": ("InvitationsPane", "default"),
            "workspaces.audit": ("AuditTrailPane", "default"),
            "workspaces.invite": ("InviteAcceptPage", "default"),
        },
        embed_components={
            "workspaces.switcher": ("WorkspaceSwitcher", "default"),
        },
    ),
    # ── Pairs with no npm release yet ───────────────────────────────────────
    # "released": False mirrors the backend row: the pair is never installed
    # from npm (no version is served). Under --allow-unreleased the scaffold
    # packs the sibling stapel-react checkout's built package into the
    # project (frontend/vendor/, a `file:` dependency); without it the backend
    # row refuses the selection first. "version" is the planned first release.
    #
    # `feed.home` is the community's first tab (order 1); the post and
    # author pages are addressed by id, off the menu.
    #
    # A "Posts" button on a public profile (`profiles.public`'s
    # page.actions) and the phone composer button.
    "feed": ReactBinding(
        package="@stapel/feed-react",
        version="0.1.0",
        provider="FeedProvider",
        create_runtime="createFeedRuntime",
        register_i18n="registerFeedI18n",
        released=False,
        components={
            "feed.home": ("FeedPage", "default"),
            "feed.post": ("PostPage", "default"),
            "feed.author": ("AuthorFeedPane", "default"),
        },
        embed_components={
            "feed.author_posts_action": ("AuthorPostsButton", "default"),
            "feed.compose_fab": ("ComposeFab", "default"),
        },
    ),
    # The booking page and the booking detail are addressed by id; the two
    # histories hang from the container-owned `account.root`, and the
    # schedule editor is linked from the agenda (off the menu).
    #
    # "Book" on a listing (`listings.detail`'s page.actions) and the
    # upcoming-bookings badge in the top bar.
    "reservations": ReactBinding(
        package="@stapel/reservations-react",
        version="0.1.0",
        provider="ReservationsProvider",
        create_runtime="createReservationsRuntime",
        register_i18n="registerReservationsI18n",
        released=False,
        components={
            "reservations.book": ("BookPage", "default"),
            "reservations.mine": ("MyReservationsPane", "default"),
            "reservations.detail": ("ReservationDetailPane", "default"),
            "reservations.agenda": ("ProviderAgendaPane", "default"),
            "reservations.schedule": ("ScheduleEditor", "default"),
        },
        embed_components={
            "reservations.book_action": ("BookButton", "default"),
            "reservations.upcoming_badge": ("UpcomingBadge", "default"),
        },
    ),
}
