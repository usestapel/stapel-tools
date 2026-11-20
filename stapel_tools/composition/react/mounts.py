"""How the React container MOUNTS each nav entry's component.

Read through the legacy ``_frontend_templates.NAV_ENTRY_MOUNTS`` view
(``composition.react.registry.nav_entry_mounts``), which adds the neutral
``page_slots`` back.
"""
from __future__ import annotations

# How a nav entry's component is MOUNTED — read off each component's own prop
# interface in the sibling stapel-react checkout, never guessed. This is the
# table that keeps generation from emitting a mount that does not compile, and
# it is the reason spec §6.1 item 11 (a placeholder that NAMES the absence) is
# mechanical rather than a promise.
#
# Four shapes:
#   {}                       zero REQUIRED props — mount it directly.
#   {"route_params": {...}}  the component needs the route's own parameters
#                            (`:id`, `:slug`); generation emits a one-file page
#                            wrapper that reads them via `useParams()` and says
#                            so when the address does not parse.
#   {"adapter": (hook, sub)} the component needs a binding the pair itself
#                            publishes on another subpath (search's
#                            `useRouterSearchParams` from `./router` — the pair
#                            keeps react-router out of its main entry, so the
#                            container supplies the binding).
#   {"container": (...)}     the component needs props only the CONTAINER can
#                            supply — the cross-pair slots spec §6.2 lists as
#                            wiring. Generation emits the placeholder; it does
#                            NOT fabricate an empty value, because an empty
#                            feature schema is not "no schema yet", it is "this
#                            category has no attributes", which is a lie with a
#                            straight face.
#   {"local": "Name"}        a container-local component this generator writes.
#
# Which page slots an entry's page renders is neutral (``BrickUi.page_slots``
# in ``composition/bricks.py``); the legacy ``NAV_ENTRY_MOUNTS`` view puts it
# back as ``page_slots`` after ``route_params``. `page_slots_since` is the
# first version of the owning React pair whose component takes the `slots`
# prop; with an older mirror the page renders no page slot.
#
# An id ABSENT from this table falls through to the placeholder, deliberately:
# a pair that adds a screen this stapel-tools does not know about gets a page
# naming that fact, not a mount that may or may not compile. The test
# `test_nav_entry_mounts_covers_every_registered_entry` keeps the gap visible.
REACT_MOUNTS: dict[str, dict] = {
    # auth-react 0.17.1 — AuthPanel/QrConfirmPanel/SecuritySettings: every
    # prop optional (src/default/*.tsx).
    "auth.login": {},
    "auth.qr_confirm": {},
    "auth.security": {},
    # auth-react 0.17.1 admin skin (src/default/admin/*.tsx) — all five panels
    # take NO props at all: each reads its data off the pair's own runtime, and
    # the four that read a staff-only list answer a refused read with the
    # pair's own `ForbiddenState` (`forbidden.tsx`). The container hands over
    # nothing; the staff gate around the whole subtree is still its own.
    "admin.sso_orgs": {},
    "admin.service_keys": {},
    "admin.staff_roles": {},
    "admin.users_create": {},
    "admin.auth_audit": {},
    # notifications-react 0.11.0 — NotificationsPage: every prop optional.
    "notifications.feed": {},
    # notifications-react 0.11.0 — PushSettingsPane requires `getToken()`, the
    # function that mints a push subscription token from the host's OWN service
    # worker / native bridge. There is no service worker in a generated
    # container to mint one from, and a stub returning a fake token registers a
    # device that can never be delivered to — so the entry gets the placeholder
    # naming the prop.
    "notifications.push": {"container": ("getToken",)},
    # profiles-react 0.26.2 — ProfileSettings/LanguageSettings/
    # NotificationPreferences/ContactsManager/ConnectionsPage: every prop
    # optional (`ContactsManagerProps` is mode/surface/gutter, all skin).
    # PublicProfilePage requires `userId`, which its own route carries.
    "profiles.settings": {},
    "profiles.language": {},
    "profiles.notifications": {},
    "profiles.contacts": {},
    "profiles.connections": {},
    # profiles-react 0.28.0 renders `page.actions` from `slots` (chat-react's
    # `chat.message_user` and feed-react's `feed.author_posts_action` scope
    # there); 0.27.x has no `slots` prop, so below that the page renders no
    # page slot and a scoped embed is dropped with a note.
    "profiles.public": {
        "route_params": {"userId": "string"},
        "page_slots_since": "0.28.0",
    },
    # categories-react 0.2.0
    "categories.catalog": {},
    "categories.category": {
        "route_params": {"slug": "string"},
        # The page leaves the listing grid to its container (a named
        # placeholder otherwise). A list of alternatives: the first whose
        # pairs are all installed fills the slot.
        #
        # 1. search + listings (T15): the category's search results drawn as
        #    `ListingFeedCard`s. Every composite that selects search ships the
        #    `listing` source (stapel-preset-gate, SH1), so the doc type is a
        #    fact of the selection, not a guess. The hit's stored card is
        #    mapped onto the listing card the feed card reads.
        # 2. listings alone: listings-react 0.42's `ListingBoardPane` over
        #    the public board narrowed by stapel-listings 0.27's
        #    `?category_id=` filter.
        "slot_fills": {
            "renderListings": [
                {
                    "pairs": ("search", "listings"),
                    "imports": (
                        ("SearchPage", "@stapel/search-react/default"),
                        ("useRouterSearchParams", "@stapel/search-react/router"),
                        ("ListingFeedCard", "@stapel/listings-react/default"),
                    ),
                    "type_imports": (
                        ("SearchItem", "@stapel/search-react"),
                        ("ListingCard", "@stapel/listings-react"),
                    ),
                    "hooks": ("const searchAdapter = useRouterSearchParams();",),
                    "helpers": (
                        "/** A search hit's stored card as the listing card `ListingFeedCard` draws. */",
                        "function listingCardFromHit(item: SearchItem): ListingCard {",
                        "  const card = item.card;",
                        "  const images = Array.isArray(card.images)",
                        "    ? card.images.filter((ref): ref is string => typeof ref === \"string\")",
                        "    : [];",
                        "  return {",
                        "    id: Number(item.key),",
                        "    title: typeof card.title === \"string\" ? card.title : \"\",",
                        "    price: typeof card.price === \"string\" ? card.price : null,",
                        "    currency: typeof card.currency === \"string\" ? card.currency : \"\",",
                        "    images,",
                        "    features_title: [],",
                        "    features_badges: [],",
                        "    location_label: typeof card.location_label === \"string\" ? card.location_label : \"\",",
                        "    is_favorited: null,",
                        "  };",
                        "}",
                    ),
                    "jsx": (
                        "(category) => (\n"
                        "        <SearchPage\n"
                        "          adapter={searchAdapter}\n"
                        "          defaultType=\"listing\"\n"
                        "          defaultCategory={String(category.id)}\n"
                        "          categoryFilter={false}\n"
                        "          searchBox={false}\n"
                        "          renderCard={(item) => <ListingFeedCard listing={listingCardFromHit(item)} href={`/l/${item.key}`} />}\n"
                        "        />\n"
                        "      )"
                    ),
                },
                {
                    "pairs": ("listings",),
                    "imports": (("ListingBoardPane", "@stapel/listings-react/default"),),
                    "jsx": "(category) => (\n        <ListingBoardPane categoryIds={[String(category.id)]} hrefFor={(id) => `/l/${id}`} />\n      )",
                },
            ],
        },
    },
    # chat-react 0.2.0 — ConversationListPanel: limit/openHref/onOpen, all
    # optional.
    "chat.conversations": {},
    # The chat-react release with `chat.thread` (`chat/:id`):
    # ConversationThreadPage requires `id`, its own route param. Mounted once
    # the registry mirror carries the entry (unreleased pair today).
    "chat.thread": {"route_params": {"id": "string"}},
    # listings-react 0.2.0; 0.43.0 renders `page.actions` from its `slots`
    # prop (a node, or a function of the route params `{ id }`).
    "listings.detail": {"route_params": {"id": "number"}},
    # The composer is the fleet's one CROSS-PAIR screen, and it used to be the
    # scaffold's biggest lie by omission: the entry mounted a placeholder, so
    # the scripted storefront had no way to list anything. Everything it needs
    # is knowable from the pairs' own manifests (categories supplies the
    # feature schema and the picker, cdn supplies the photo queue and grid), so
    # generation WIRES it — and NAMES the one slot it still cannot fill.
    "listings.compose": {"composite": "listings.compose"},
    "listings.mine": {},
    "listings.favorites": {},
    # search-react 0.2.0 — SearchPage's `adapter` is REQUIRED and the pair
    # deliberately does not import react-router from its main entry (§13.4
    # item 6), so the binding comes from its own `./router` subpath.
    # SearchPage also requires `defaultType` — the doc type to search when the
    # URL carries none. That is a DEPLOYMENT fact (`STAPEL_SEARCH["SOURCES"]`
    # names the types this fleet indexes), so it is satisfiable from a
    # generator option and never from a guess: `--doc-type listing` mounts the
    # page, and its absence gets the placeholder naming the prop. Guessing it
    # would send every search to a type the backend refuses, on a page that
    # looks perfectly wired.
    "search.results": {
        "adapter": ("useRouterSearchParams", "router"),
        "option_props": {"defaultType": "doc_type"},
    },
    "search.ranking": {},
    # gdpr-react 0.3.0 — PrivacyPane/PrivacyAdminPane/PrivacyRequestPane:
    # every prop optional. The request pane's `captcha` slot stays unfilled:
    # a public DSAR form with no captcha is the pair's own default, and a
    # container cannot invent a provider's widget.
    "account.privacy": {},
    # translate-react 0.2.0 — LanguageSettingsPane: every prop optional.
    "account.language": {},
    "admin.privacy": {},
    "public.privacy-request": {},
    # video-react 0.2.1 — ScopeUsagePane/RoomsPane: every prop optional.
    "admin.usage": {},
    "video.rooms": {},
    # billing-react 0.10.0 — WalletPanel's only prop is `onCheckoutUrl`, and
    # it is optional: omitted, the pair sends the browser to the hosted
    # checkout with `location.assign`. A container that passed its router's
    # navigate would be handing a react-router navigate an off-origin URL.
    "account.billing": {},
    # calendar-react 0.8.0 — Calendar/AvailabilityPane: every prop optional.
    # `viewerId` is among them, so owner-only controls stay hidden until a
    # host supplies one; that is the pair's own designed degradation, not a
    # slot this generator can fill from a session it does not read.
    # With auth installed the signed-in user's id is the `viewerId` (Calendar
    # hands it on to its EventSheet): without it RSVP and edit stay hidden for
    # everyone. A `CalendarPeopleProvider` names the viewer instead of
    # printing their id; other people stay ids until a directory exists.
    "calendar.month": {"viewer": "auth"},
    "calendar.availability": {},
    # forms-react 0.3.1 — `workspaceId` is optional on all three (the runtime's
    # active selection answers it, which is exactly what the routable case
    # means), but the builder and the responses table both REQUIRE `formId`,
    # and their routes carry it.
    "forms.list": {},
    "forms.builder": {"route_params": {"formId": "string"}},
    "forms.responses": {"route_params": {"formId": "string"}},
    # recordings-react 0.6.1 — RecordingsList: every prop optional (`onOpen`
    # omitted renders static rows; the detail route is reached from the menu).
    # The other two require the id their own address carries, and `linkToken`
    # is a BEARER SECRET: it belongs in the route and nowhere else.
    "recordings.list": {},
    "recordings.detail": {"route_params": {"recordingId": "string"}},
    "share.view": {"route_params": {"linkToken": "string"}},
    # moderation-react 0.1.1 — all four screens take ZERO required props, read
    # off the pair's own prop interfaces rather than assumed:
    # `PolicyDisclosurePaneProps` (optional `targetType`), `AppealPanelProps`
    # (optional `caseId`/`sanctionId`, which the pair's own
    # `DEFAULT_APPEAL_HREF` puts in the QUERY STRING — `?case=` — not in the
    # path, so there is no route param to thread), `ModerationQueueProps`
    # (optional `viewerId`: the module has no `/me` and a container that
    # invented one would mislabel a colleague's lease as the reader's own,
    # so it stays unset and every lease reads as somebody's) and
    # `AppealsQueueProps` (nothing but `data-testid`). Each also extends
    # `ThemeModeProp`, whose `mode` is optional BY DESIGN — a pair that
    # defaulted it is how a dark host gets one white rectangle.
    "moderation.policy": {},
    "account.appeals": {},
    "admin.moderation": {},
    "admin.moderation-appeals": {},
    # workspaces-react 0.19.0 — every account screen's `workspaceId` is
    # optional BECAUSE this nav contract routes it: with none, the active
    # workspace comes from the runtime selection and a screen with no
    # selection renders the pair's own "choose a workspace" state. The invite
    # page is the exception and the only required prop in the pair: `token`,
    # off its own public `/invite/:token` route.
    "workspaces.list": {},
    "workspaces.settings": {},
    "workspaces.members": {},
    "workspaces.invitations": {},
    "workspaces.audit": {},
    "workspaces.invite": {"route_params": {"token": "string"}},
    # the container's own.
    # feed-react 0.1.0: FeedPage takes the picture queue from the container
    # (cdn-react's upload bag); the post and author pages read their ids.
    "feed.home": {"composite": "feed.home"},
    "feed.post": {"route_params": {"id": "number"}},
    "feed.author": {"route_params": {"userId": "string"}},
    # reservations-react 0.1.0: the booking page reads the bookable from its
    # address; the detail and the schedule editor read their ids.
    # orders-react 0.2.0: the cart, checkout and history take only optional
    # links (their defaults are the pair's own routes); one order by its id.
    "orders.cart": {},
    "orders.checkout": {},
    "orders.mine": {},
    "orders.detail": {"route_params": {"id": "number"}},
    "reservations.book": {"route_params": {"bookableType": "string", "bookableKey": "string"}},
    "reservations.mine": {},
    "reservations.detail": {"route_params": {"id": "number"}},
    "reservations.agenda": {},
    "reservations.schedule": {"route_params": {"id": "number"}},
    "account.root": {"local": "AccountHome"},
    "admin.root": {"local": "AdminHome"},
}
