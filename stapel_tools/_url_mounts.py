"""Single source of truth for a Stapel feature lib's Django URL mount prefix
— the exact string a service's root urlconf passes to
``path(<prefix>, include(<app>.urls))`` when wiring a lib in.

Closes the "generated monolith mismounts every lib" bug: ``new_service.py``
used to mount EVERY feature lib under the hosting SERVICE's own shared
``{url_prefix}api/`` (its slug, e.g. ``"app/api/"`` for a monolith) — so two
libs selected into the same monolith collided on the identical path, and only
``stapel_cdn`` was hand-special-cased to its real ``"cdn/api/"`` mount. A
dedicated single-lib microservice hid the bug whenever the service's own slug
happened to equal the lib's key (the common case) — it broke the moment a
monolith combined libs, or a microservice's slug diverged from its lib's key.

Derived from ``STAPEL_LIBS`` (``create_project.py``) rather than a duplicated
table, so there is exactly one registry to keep in sync: a lib's own
``url_prefix`` field there already distinguishes the two mount shapes
(cross-checked lib-by-lib against each sibling checkout's own urls.py /
urls_v1.py, not merely trusted from the registry, and against the one
hand-wired working reference, meettoday's own
``.../eil meet/backend/config/urls.py``, for auth/workspaces/profiles/
notifications/calendar/recordings/cdn):

* a lib whose own ``urls.py`` does NOT nest an ``api/`` segment (it starts
  straight at ``path("v1/", include(...))``) needs the HOST to supply it —
  mount at ``"<mod>/api/"`` (the "first 8" onboarded libs' documented legacy
  default: auth, billing, cdn, notifications, profiles, workspaces, gdpr,
  plus categories/listings which read the SAME way despite being onboarded
  in the "bakes api/ into its own urls" second wave — see the override note
  in ``create_project.STAPEL_LIBS`` for those two);
* a lib whose own ``urls.py`` already nests ``path("api/v1/", include(...))``
  (or, for currencies, bakes an ``api/v1`` router prefix) contributes that
  segment itself — mount BARE at ``"<mod>/"``: calendar, recordings, agent,
  chat, geo, mailtrap, reviews, tasks, video, currencies;
* ``"http": False`` libs (attributes, vault — pure pip deps; shop/classified/
  booking/social — Projection-glue-only composites) have no urls.py at all —
  mount nothing.

One true outlier needs an explicit override below: stapel_translate's own
``urls_v1.py`` hardcodes its FULL ``"translate/api/v1/..."`` prefix
internally (unlike every other lib, which relies on the host to supply the
``"<mod>/"`` segment) — mounting it again under a host-supplied prefix would
double the segment.
"""
from __future__ import annotations

# Outliers whose correct Django mount contradicts the generic STAPEL_LIBS
# derivation below — each documented with the specific urls.py quirk
# responsible, so a future reader doesn't "fix" this back to the registry
# default.
_MOUNT_OVERRIDES: dict[str, str] = {
    # stapel_translate/urls.py is a bare `path('', include('...urls_v1'))`
    # pass-through; urls_v1.py hardcodes its OWN full "translate/api/v1/..."
    # prefix internally (api-versioning.md §2/§6 canon baked in at the
    # source, unlike every other lib). Mounting it again under
    # "translate/api/" would double the segment to
    # ".../api/translate/api/v1/...". Mount at the bare project root instead
    # — its own patterns already carry the "translate/" segment.
    "stapel_translate": "",
}


def known_apps() -> set[str]:
    """Django app/dir names this module can resolve a mount for (every
    ``STAPEL_LIBS`` entry's ``"dir"``, regardless of http/headless status —
    callers combine this with :func:`url_mount_for` to tell "headless, mount
    nothing" (``None``) apart from "not a registered Stapel lib at all"."""
    from .create_project import STAPEL_LIBS

    return {info["dir"] for info in STAPEL_LIBS.values()}


def url_mount_for(app: str) -> str | None:
    """The Django mount prefix for *app* (a Stapel lib's Django app/dir name,
    e.g. ``"stapel_auth"``). ``None`` means "headless — mount nothing" (a
    composite's glue-only app, or a pure-pip lib like attributes/vault with
    no urls.py of its own).

    Raises ``KeyError`` for any *app* not in ``STAPEL_LIBS`` — check
    ``app in known_apps()`` first; callers scaffolding a project-local/custom
    app (not a registered Stapel lib) keep their own fallback instead of
    calling this."""
    if app in _MOUNT_OVERRIDES:
        return _MOUNT_OVERRIDES[app]
    from .create_project import STAPEL_LIBS

    key = app.removeprefix("stapel_")
    info = STAPEL_LIBS[key]  # KeyError if unknown — caller's job to guard
    if not info.get("http", True):
        return None
    prefix = info.get("url_prefix")
    return prefix if prefix is not None else f"{key}/api/"
