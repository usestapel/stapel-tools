"""Excerpt of stapel-notifications HEAD tests/test_i18n_loop.py.

The undeclared one that is still live: the loop test imports stapel-translate
from inside the test body, behind a skip that reads a REGISTRY, not the
package. If translate is absent the import raises before that skip can help.
"""
import pytest


def _translate_resolve_registered():
    from stapel_core.comm import function_registry

    return "translate.resolve" in function_registry.names()


@pytest.mark.django_db
def test_translate_to_notifications_loop_updates_cache_and_rendered_email():
    if not _translate_resolve_registered():
        pytest.skip("translate.resolve Function is not registered")

    from stapel_translate.events import emit_translations_changed
    from stapel_translate.models import TranslationEntry

    assert emit_translations_changed and TranslationEntry
