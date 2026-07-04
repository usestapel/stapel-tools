"""Integration test-harness templates injected into every generated project.

The harness turns the transactional outbox (stapel_core.django.outbox) and a
file-based email trap into synchronously testable building blocks, so async
flows split into deterministic halves without sleeps (system-design §7.12.3,
§7.21):

    tests/harness/outbox.py    -> drain_outbox()  (synchronous dispatch)
    tests/harness/mailtrap.py  -> FileMailtrapBackend + read/clear helpers
    tests/harness/wait.py      -> wait_for(condition, timeout)
    tests/test_outbox_harness_example.py  -> producer/consumer/atomicity proof

`harness_files(tests_dir)` returns {absolute Path: content} for the shared,
preset-independent files; each preset wires the settings + Makefile bits.
"""
from __future__ import annotations

from pathlib import Path

HARNESS_INIT = '''\
"""Integration test harness: transactional-outbox + file-mailtrap helpers.

    from tests.harness import drain_outbox, wait_for, read_mailtrap
"""
from .mailtrap import FileMailtrapBackend, clear_mailtrap, mailtrap_dir, read_mailtrap
from .outbox import drain_outbox
from .wait import WaitTimeout, wait_for

__all__ = [
    "FileMailtrapBackend",
    "clear_mailtrap",
    "mailtrap_dir",
    "read_mailtrap",
    "drain_outbox",
    "WaitTimeout",
    "wait_for",
]
'''

HARNESS_WAIT = '''\
"""wait_for — poll a condition until it holds, instead of sleeping blindly.

For genuinely asynchronous effects (a broker round-trip, a background worker).
For the in-process outbox prefer drain_outbox(), which is fully deterministic
and needs no waiting at all (system-design §7.21: no sleeps in tests).
"""
from __future__ import annotations

import time
from collections.abc import Callable


class WaitTimeout(AssertionError):
    """Raised when wait_for's condition stays falsy past the timeout."""


def wait_for(
    condition: Callable[[], object],
    timeout: float = 2.0,
    interval: float = 0.01,
    message: str | None = None,
):
    """Call condition() until it returns a truthy value; return that value.

    Raises WaitTimeout after ``timeout`` seconds. ``interval`` is the poll gap.
    """
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise WaitTimeout(message or f"condition not met within {timeout}s")
        time.sleep(interval)
'''

HARNESS_OUTBOX = '''\
"""drain_outbox — synchronously deliver pending outbox rows in tests.

The production relay (``manage.py dispatch_outbox``) runs continuously; in
tests we drain on demand so an async flow splits into synchronous halves:
emit writes a row (producer), drain delivers it to its observable effect
(consumer) — system-design §7.21.
"""
from __future__ import annotations


def drain_outbox(max_passes: int = 100) -> int:
    """Deliver every pending outbox row through the real delivery path
    (stapel_core.django.outbox.relay.dispatch_pending). Returns rows delivered.

    Rows are forced due first so retry backoff never makes a test wait; loops
    until the outbox is empty or a row stops making progress (permanent fail).
    """
    from django.utils import timezone
    from stapel_core.django.outbox.models import OutboxEvent
    from stapel_core.django.outbox.relay import dispatch_pending

    delivered_total = 0
    for _ in range(max_passes):
        pending = OutboxEvent.objects.filter(dispatched_at__isnull=True)
        if not pending.exists():
            break
        pending.update(next_attempt_at=timezone.now())
        delivered, _failed = dispatch_pending(limit=1000)
        delivered_total += delivered
        if delivered == 0:
            break
    return delivered_total
'''

HARNESS_MAILTRAP = '''\
"""File mailtrap: an email backend that writes each message to var/mailtrap/
as a machine-readable JSON file, plus read/clear helpers for tests and dev.

Point EMAIL_BACKEND at FileMailtrapBackend to make outbound mail inspectable
without a real SMTP server — the observable effect an async-consumer test
asserts on (system-design §7.21).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


def mailtrap_dir() -> Path:
    configured = getattr(settings, "MAILTRAP_DIR", None)
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR) / "var" / "mailtrap"


class FileMailtrapBackend(BaseEmailBackend):
    """Write each EmailMessage to var/mailtrap/<ns>.json."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        directory = mailtrap_dir()
        directory.mkdir(parents=True, exist_ok=True)
        written = 0
        for index, message in enumerate(email_messages):
            record = {
                "subject": message.subject,
                "from": message.from_email,
                "to": list(message.to),
                "cc": list(message.cc),
                "bcc": list(message.bcc),
                "reply_to": list(message.reply_to),
                "body": message.body,
                "content_subtype": message.content_subtype,
                "alternatives": [
                    {"content": content, "mimetype": mimetype}
                    for content, mimetype in getattr(message, "alternatives", []) or []
                ],
                "headers": dict(message.extra_headers or {}),
            }
            name = f"{time.time_ns()}-{index:03d}.json"
            (directory / name).write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            written += 1
        return written


def read_mailtrap() -> list[dict]:
    """Return all trapped messages, oldest first (sorted by filename)."""
    directory = mailtrap_dir()
    if not directory.exists():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def clear_mailtrap() -> None:
    """Delete every trapped message. Safe when the directory is absent."""
    directory = mailtrap_dir()
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        path.unlink()
'''

HARNESS_EXAMPLE_TEST = '''\
"""Example: the transactional-outbox test pattern (system-design §7.21).

An async flow is split into synchronous, deterministic halves:
  * producer  — an action emitted inside a DB transaction becomes an outbox row
  * consumer  — draining the outbox runs the row through delivery to its effect
  * atomicity — a rolled-back transaction leaves NO row (events never lie about
                state that did not commit)

Copy this pattern for real action/consumer pairs; delete this file once the
suite has its own outbox tests. It exists to prove the harness is wired.
"""
import pytest
from django.db import transaction
from stapel_core.comm import emit, subscribe_action
from stapel_core.django.outbox.models import OutboxEvent


@pytest.mark.django_db
def test_producer_emit_inside_transaction_writes_outbox_row():
    with transaction.atomic():
        emit("harness.ping", {"n": 1})
    # django_db suppresses on_commit, so the row stays pending — proof the event
    # was persisted transactionally rather than delivered eagerly.
    row = OutboxEvent.objects.get()
    assert row.topic == "harness.ping"
    assert row.dispatched_at is None


@pytest.mark.django_db
def test_consumer_drain_delivers_event_to_subscriber(drain_outbox):
    received = []
    subscribe_action("harness.ping", lambda event: received.append(event.payload))

    with transaction.atomic():
        emit("harness.ping", {"n": 2})
    assert received == []  # not delivered until the outbox drains

    delivered = drain_outbox()
    assert delivered == 1
    assert received == [{"n": 2}]


@pytest.mark.django_db
def test_rolled_back_transaction_writes_no_outbox_row():
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with transaction.atomic():
            emit("harness.ping", {"n": 3})
            raise Boom
    assert OutboxEvent.objects.count() == 0


@pytest.mark.django_db
def test_consumer_side_effect_lands_in_file_mailtrap(drain_outbox, mailtrap):
    from django.core.mail import send_mail

    def send_welcome(event):
        send_mail(
            "Welcome",
            f"Hi {event.payload['name']}",
            "noreply@example.com",
            ["user@example.com"],
        )

    subscribe_action("harness.welcome", send_welcome)

    with transaction.atomic():
        emit("harness.welcome", {"name": "Ada"})
    assert mailtrap() == []  # no mail until the outbox drains

    drain_outbox()

    messages = mailtrap()
    assert len(messages) == 1
    assert messages[0]["subject"] == "Welcome"
    assert messages[0]["to"] == ["user@example.com"]
    assert "Ada" in messages[0]["body"]
'''

# Fixture block shared by every preset's conftest. Assumes a top-level `tests`
# package so `from tests.harness import ...` resolves (tests/__init__.py).
HARNESS_CONFTEST_FIXTURES = '''\
import pytest

from tests.harness import clear_mailtrap, read_mailtrap
from tests.harness import drain_outbox as _drain_outbox


@pytest.fixture(autouse=True)
def _reset_comm_registries():
    """Isolate action subscribers between tests (the registry is process-global)."""
    from stapel_core.comm import action_registry, function_registry

    action_registry.clear()
    function_registry.clear()
    yield
    action_registry.clear()
    function_registry.clear()


@pytest.fixture(autouse=True)
def outbox_comm(settings):
    """Run comm in-process with the transactional outbox ENABLED against the
    test DB — the production delivery path (emit -> outbox row -> dispatch),
    so producer/consumer/atomicity assertions exercise real behaviour."""
    settings.STAPEL_COMM = {
        **getattr(settings, "STAPEL_COMM", {}),
        "OUTBOX_ENABLED": True,
        "ACTION_TRANSPORT": "inprocess",
    }


@pytest.fixture
def drain_outbox():
    """Synchronously flush pending outbox rows through delivery (the test-time
    stand-in for ``manage.py dispatch_outbox``). Returns rows delivered."""
    return _drain_outbox


@pytest.fixture
def mailtrap(settings):
    """File mailtrap: force the file email backend, clear var/mailtrap/, then
    yield read_mailtrap(). pytest-django swaps EMAIL_BACKEND to locmem by
    default; async-consumer tests assert on the on-disk trap instead."""
    settings.EMAIL_BACKEND = "tests.harness.mailtrap.FileMailtrapBackend"
    clear_mailtrap()
    yield read_mailtrap
    clear_mailtrap()
'''


def harness_files(tests_dir: Path) -> dict[Path, str]:
    """Return {path: content} for the preset-independent harness files, rooted
    at *tests_dir* (the project's top-level ``tests`` package)."""
    harness = tests_dir / "harness"
    return {
        tests_dir / "__init__.py": "",
        harness / "__init__.py": HARNESS_INIT,
        harness / "wait.py": HARNESS_WAIT,
        harness / "outbox.py": HARNESS_OUTBOX,
        harness / "mailtrap.py": HARNESS_MAILTRAP,
        tests_dir / "test_outbox_harness_example.py": HARNESS_EXAMPLE_TEST,
    }
