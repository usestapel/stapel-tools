"""The same excerpt at stapel-chat HEAD: stapel-cdn is DECLARED, and the skip
guard is only tolerable because the workflow sets STAPEL_TEST_STRICT_SIBLINGS=1
— on CI the missing package fails instead of skipping. Drop that env line from
.github/workflows/ci.yml and SIB004 fires on this very file.
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
