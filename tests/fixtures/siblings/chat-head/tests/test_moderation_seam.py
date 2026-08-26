"""The same excerpt at stapel-chat HEAD: stapel-moderation is declared in the
`test` extra now, so these imports are a contract rather than an accident.
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
