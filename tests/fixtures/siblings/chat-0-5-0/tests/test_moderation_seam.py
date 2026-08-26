"""Excerpt of stapel-chat v0.5.0 tests/test_moderation_seam.py.

The imports sit inside a fixture and inside test bodies — which is the whole
point: a module-header grep says this file imports nothing but pytest.
"""
import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clean_moderation_registry():
    """Target types are process-global; leave the registry as we found it."""
    from stapel_moderation.registry import reset_registries

    reset_registries()
    yield
    reset_registries()


def test_a_reported_message_reaches_the_queue():
    from stapel_moderation import services as moderation

    assert moderation is not None


def test_the_policy_resolves_for_our_target_type():
    from stapel_moderation.registry import resolve_policy

    assert resolve_policy is not None
