#!/usr/bin/env python
"""Brick flows over a freshly generated monolith: the feed and the booking.

The live circle (``e2e_live_circle.py``) proves a generated project signs a
person in. This proves the two companion bricks work in the project the
scaffold assembled, with zero hand edits, the way their pairs' ``flows.json``
walk them in a browser (SPEC-NEXT-BRICKS §A.7, §B.5):

``feed`` (``--modules social``)
    two members sign up; one follows the other; the author posts; the post is
    in the follower's home feed; the follower likes it (count 1) and comments
    (comment_count 1).

``reservations`` (``--modules booking``)
    a provider publishes a service listing and saves its schedule; anonymous
    slots answer 200; an anonymous booking is refused (account required); a
    signed-in customer books (201) and sees it in the history; a second
    customer is refused the same slot (409 slot_full); the customer cancels a
    slot more than a day ahead and it is free again.

Run from the generated project's SERVICE directory, with the project's
requirements installed and a reachable Postgres, as the live circle is::

    python scripts/e2e_brick_flows.py feed|reservations

Every URL is ``reverse()``d against the project's own urlconf. Exit 0 = the
flow drives; any assertion = non-zero.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_live_circle import load_env_local  # noqa: E402


def _setup():
    env = Path("..") / ".env.local"
    if env.is_file():  # absent inside the service container, whose env is set
        load_env_local(env)
    os.environ["DJANGO_SETTINGS_MODULE"] = os.environ.get("E2E_SETTINGS", "config.settings.dev")
    import django

    django.setup()
    from django.core.management import call_command

    call_command("migrate", "--noinput", verbosity=0)


class Member:
    """A signed-in member: a Django test client carrying the bearer token."""

    def __init__(self, label: str):
        from django.test import Client
        from django.urls import reverse
        from stapel_auth.conf import auth_settings

        self.client = Client()
        self.email = f"{label}+{int(time.time() * 1000)}@example.com"
        r = self.client.post(reverse("email_request"), {"email": self.email},
                             content_type="application/json")
        assert r.status_code in (200, 201), (r.status_code, r.content[:300])
        r = self.client.post(reverse("email_verify"),
                             {"email": self.email, "code": auth_settings.MOCK_OTP_CODE},
                             content_type="application/json")
        assert r.status_code in (200, 201), (r.status_code, r.content[:300])
        body = json.loads(r.content)
        self.id = body["user"]["id"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {body['tokens']['access']}"}

    def call(self, method: str, url: str, body=None):
        fn = getattr(self.client, method.lower())
        if body is None:
            return fn(url, **self.auth)
        return fn(url, json.dumps(body), content_type="application/json", **self.auth)


def _json(r):
    return json.loads(r.content) if r.content else None


def _png() -> bytes:
    """A 32x32 RGB PNG: a listing publishes with at least one image."""
    import struct
    import zlib

    raw = b"".join(
        b"\x00" + b"".join(bytes((x * 8, y * 8, 128)) for x in range(32)) for y in range(32))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def feed() -> None:
    from django.urls import reverse

    author, follower = Member("author"), Member("follower")
    r = follower.call("POST", reverse("follow-user", kwargs={"user_id": author.id}))
    assert r.status_code in (200, 201), ("follow", r.status_code, r.content[:300])

    r = author.call("POST", reverse("feed-posts"), {"body": "Hello from the flow"})
    assert r.status_code == 201, ("post", r.status_code, r.content[:300])
    post = _json(r)["id"]
    print(f"e2e feed: post {post} created")

    r = follower.call("GET", reverse("feed-home"))
    assert r.status_code == 200, ("home", r.status_code)
    assert post in [p["id"] for p in _json(r)["items"]], ("home feed lacks the post", _json(r))

    r = follower.call("PUT", reverse("feed-reactions"),
                      {"target_type": "post", "target_key": str(post), "kind": "like"})
    assert r.status_code == 200, ("react", r.status_code, r.content[:300])
    assert _json(r)["active"] is True and _json(r)["reaction_counts"].get("like") == 1, _json(r)

    r = follower.call("POST", reverse("feed-post-comments", kwargs={"post_id": post}),
                      {"body": "First comment"})
    assert r.status_code == 201, ("comment", r.status_code, r.content[:300])

    r = author.call("GET", reverse("feed-post", kwargs={"post_id": post}))
    body = _json(r)
    assert body["comment_count"] == 1 and body["reaction_counts"].get("like") == 1, body
    print("e2e feed: OK — follow -> post -> home feed -> like -> comment")


def reservations() -> None:
    from django.core.management import call_command
    from django.test import Client
    from django.urls import reverse

    # The starter catalog the generated bootstrap seeds (categories' "seed").
    call_command("load_catalog", "--seed-if-empty")
    category = _json(Client().get(reverse("category-roots")))[0]["id"]

    provider = Member("provider")
    # A service: countable=False (listings.bookables answers available=False
    # for a countable listing with no stock).
    r = provider.call("POST", reverse("listing-list"), {"currency": "USD", "countable": False})
    assert r.status_code == 201, ("listing", r.status_code, r.content[:300])
    listing = _json(r)["id"]
    from django.core.files.uploadedfile import SimpleUploadedFile

    r = provider.client.post(
        reverse("typed-image-upload", kwargs={"image_type": "product"}),
        {"file": SimpleUploadedFile("chair.png", _png(), content_type="image/png")}, **provider.auth)
    assert r.status_code in (200, 201), ("image", r.status_code, r.content[:300])
    image = _json(r)["image"]["prefix"]
    r = provider.call("POST", reverse("listing-save-draft", kwargs={"pk": listing}), {
        "category_id": str(category), "title_draft": "Haircut", "images_draft": [image],
        "description_draft": "Thirty minutes in the chair", "price_draft": "20.00"})
    assert r.status_code == 200, ("draft", r.status_code, r.content[:300])
    r = provider.call("POST", reverse("listing-publish", kwargs={"pk": listing}), {})
    assert r.status_code == 200, ("publish", r.status_code, r.content[:500])

    key = {"bookable_type": "listing", "bookable_key": str(listing)}
    schedule_url = reverse("reservations-schedule", kwargs=key)
    schedule = _json(provider.call("GET", schedule_url))
    schedule.pop("exists", None)
    r = provider.call("PUT", schedule_url, schedule)
    assert r.status_code == 200, ("schedule", r.status_code, r.content[:300])

    day = dt.date.today() + dt.timedelta(days=2)
    window = f"?start={day.isoformat()}T00:00:00Z&end={(day + dt.timedelta(days=7)).isoformat()}T00:00:00Z"
    slots_url = reverse("reservations-slots", kwargs=key) + window
    r = Client().get(slots_url)
    assert r.status_code == 200, ("anonymous slots", r.status_code)
    start = next(s["start"] for s in _json(r)["slots"] if s["bookable"])

    def booking(contact_name: str) -> dict:
        return {"idempotency_key": str(uuid.uuid4()), **key, "start": start,
                "contact": {"name": contact_name, "phone": "+12025550142"}}

    r = Client().post(reverse("reservations-list"), json.dumps(booking("Anon")),
                      content_type="application/json")
    assert r.status_code in (401, 403), ("anonymous booking", r.status_code, r.content[:300])

    customer = Member("customer")
    r = customer.call("POST", reverse("reservations-list"), booking("Alex Doe"))
    assert r.status_code == 201, ("book", r.status_code, r.content[:300])
    reservation = _json(r)["id"]
    print(f"e2e reservations: reservation {reservation} at {start}")

    r = customer.call("GET", reverse("reservations-list"))
    assert reservation in [it["id"] for it in _json(r)["items"]], ("history", _json(r))

    r = Member("second").call("POST", reverse("reservations-list"), booking("Sam Roe"))
    assert r.status_code == 409, ("double booking", r.status_code, r.content[:300])
    assert _json(r)["localizable_error"] == "error.409.slot_full", _json(r)

    r = customer.call("POST", reverse("reservations-cancel", kwargs={"reservation_id": reservation}))
    assert r.status_code == 200, ("cancel", r.status_code, r.content[:300])
    slot = next(s for s in _json(Client().get(slots_url))["slots"] if s["start"] == start)
    assert slot["bookable"] is True, slot
    print("e2e reservations: OK — schedule -> slots -> 403 -> book -> history -> 409 -> cancel")


FLOWS = {"feed": feed, "reservations": reservations}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in FLOWS:
        print(f"usage: e2e_brick_flows.py {'|'.join(FLOWS)}", file=sys.stderr)
        return 2
    _setup()
    FLOWS[sys.argv[1]]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
