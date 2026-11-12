"""Chat usable from a scaffolded app once the pair carries the thread route.

The 0.79.1 scaffold mounted only the conversation list: no `chat/:id` route,
no "message the seller" entry. The next chat-react release declares
`chat.thread` (ConversationThreadPage, `id` route param) and two scoped
`page.actions` embeds (`chat.message_seller` on `listings.detail`,
`chat.message_user` on `profiles.public`), and asks the host for
`slots.subjectOwner`. The registry mirror is regenerated from the pair's
manifest at release; this test feeds that manifest in and checks the
container wires it, and that today's mirror renders exactly what it did.
"""
import copy

import pytest

from stapel_tools import create_project as cp

NEXT_CHAT = {
    "nav_extra": {
        "id": "chat.thread", "labelKey": "chat.nav.thread", "icon": "MessageOutlined",
        "route": {"path": "chat/:id"},
        "component": {"export": "ConversationThreadPage", "subpath": "default"},
        "placement": {"level": "top"}, "menuVisibleDefault": False, "requiresAuth": True,
        "surface": "member", "order": 26,
    },
    "embeds": [
        {"id": "chat.message_seller", "slot": "page.actions",
         "component": {"export": "MessageButton", "subpath": "default"}, "order": 5,
         "surface": "any", "scope": {"navEntry": "listings.detail"},
         "routeProps": {"subjectKey": "id"}, "props": {"subjectType": "listing", "audience": "seller"}},
        {"id": "chat.message_user", "slot": "page.actions",
         "component": {"export": "MessageButton", "subpath": "default"}, "order": 5,
         "surface": "any", "scope": {"navEntry": "profiles.public"},
         "routeProps": {"userId": "userId"}, "props": {"audience": "user"}},
    ],
}


def _front(tmp_path, modules):
    cp.create_project(
        name="app", project_type="monolith", title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules,
        output_dir=tmp_path, use_submodules=False, init_git=False, want_landing=True,
    )
    return tmp_path / "app" / "frontend" / "src"


@pytest.fixture
def next_chat(monkeypatch):
    chat = copy.deepcopy(cp.FRONTEND_REACT_LIBS["chat"])
    chat["nav"] = [*chat["nav"], NEXT_CHAT["nav_extra"]]
    chat["embeds"] = NEXT_CHAT["embeds"]
    monkeypatch.setitem(cp.FRONTEND_REACT_LIBS, "chat", chat)


def test_classified_wires_thread_route_and_message_buttons(tmp_path, next_chat):
    src = _front(tmp_path, ["classified"])
    routes = (src / "routes.tsx").read_text()
    assert '{ path: "chat/:id", element: <' in routes
    thread = next(p for p in (src / "pages").glob("*.tsx") if "ConversationThreadPage" in p.read_text())
    assert "id={id}" in thread.read_text() and "useParams" in thread.read_text()
    detail = (src / "pages" / "ListingDetailPaneRoute.tsx").read_text()
    assert "MessageButton" in detail and '"page.actions"' in detail
    modules = (src / "modules.tsx").read_text()
    assert "slots: { subjectOwner: ListingSubjectOwner }" in modules
    assert 'import { useListing } from "@stapel/listings-react";' in modules


@pytest.fixture
def next_profiles(monkeypatch):
    profiles = copy.deepcopy(cp.FRONTEND_REACT_LIBS["profiles"])
    profiles["version"] = "0.28.0"  # PublicProfilePage takes `slots`
    monkeypatch.setitem(cp.FRONTEND_REACT_LIBS, "profiles", profiles)


def test_social_profile_page_gets_the_message_button(tmp_path, next_chat, next_profiles):
    src = _front(tmp_path, ["social"])
    profile = (src / "pages" / "PublicProfilePageRoute.tsx").read_text()
    assert "MessageButton" in profile and "slots={{" in profile
    # No listings in social: no subject owner seam to answer.
    assert "subjectOwner" not in (src / "modules.tsx").read_text()


def test_todays_mirror_renders_no_chat_seams(tmp_path):
    src = _front(tmp_path, ["classified"])
    assert "chat/:id" not in (src / "routes.tsx").read_text()
    assert "subjectOwner" not in (src / "modules.tsx").read_text()
    assert "slots=" not in (src / "pages" / "PublicProfilePageRoute.tsx").read_text()


def test_profile_page_slots_wait_for_the_profiles_release(tmp_path, next_chat):
    # A scoped embed alone must not hand `slots` to a PublicProfilePage that
    # has no such prop (profiles-react 0.27.x): the container would not compile.
    src = _front(tmp_path, ["social"])
    profile = (src / "pages" / "PublicProfilePageRoute.tsx").read_text()
    assert "slots=" not in profile and "MessageButton" not in profile


def test_feed_embed_waits_for_the_profiles_release(tmp_path, monkeypatch):
    monkeypatch.setenv("SIBLING_ROOT", str(tmp_path / "nowhere"))
    cp.create_project(
        name="app", project_type="monolith", title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=["social", "feed"],
        output_dir=tmp_path, use_submodules=False, init_git=False, want_landing=True,
        allow_unreleased=True,
    )
    profile = (tmp_path / "app" / "frontend" / "src" / "pages" / "PublicProfilePageRoute.tsx").read_text()
    assert "slots=" not in profile and "AuthorPostsButton" not in profile
