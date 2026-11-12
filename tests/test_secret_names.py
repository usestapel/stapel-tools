"""The CONFIG.MD secret-name heuristic: a certificate is a credential only as
what the key names, not as a word anywhere in it."""
import pytest

from stapel_tools.config_manifest import _looks_secret


@pytest.mark.parametrize("key", [
    "TLS_CERT", "SIGNING_CERTIFICATE", "CLIENT_CERT_PATH", "CERT_PEM",
    "CERTIFICATE_FILE", "SECRET_KEY", "API_TOKEN", "DB_PASSWORD",
])
def test_credential_names_are_secret(key):
    assert _looks_secret(key)


@pytest.mark.parametrize("key", [
    "CERTIFICATE_NUMBER_PREFIX", "CERTIFICATE_TEMPLATE", "CERT_REQUIRED_LESSONS",
    "ISSUE_CERTIFICATES", "PAGE_SIZE",
])
def test_certificate_as_domain_vocabulary_is_not(key):
    """Inversion: the old whole-word match called every one of the first three
    a credential, so a source = settings row for them was refused."""
    assert not _looks_secret(key)
