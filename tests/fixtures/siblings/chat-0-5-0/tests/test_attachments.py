"""Excerpt of stapel-chat v0.5.0 tests/test_attachments.py.

The quiet half of the class: a cross-module agreement test wrapped in
``try/except ImportError -> pytest.skip``. It never failed, and on CI it never
ran either, so the vocabulary agreement it claims to enforce (chat's
attachment `type` IS the CDN's media `kind`) was enforced nowhere.
"""
import pytest

BUILTIN_ATTACHMENT_TYPES = {"image": {"preview_kind": "thumb"}}


class TestCdnVocabulary:
    def test_the_type_names_ARE_the_cdn_kind_names(self):
        try:
            from stapel_cdn.kinds import BUILTIN_MEDIA_KINDS
        except ImportError:
            pytest.skip("stapel-cdn is not installed in this environment")
        assert set(BUILTIN_ATTACHMENT_TYPES) == set(BUILTIN_MEDIA_KINDS)
