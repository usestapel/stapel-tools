"""Excerpt of stapel-notifications HEAD tests/test_feed_stream.py.

stapel-realtime IS declared (through the self-referential `test` extra), so
SIB001/SIB003 are silent here — and the skip guard is still a hole, because
this repo's workflows never set STAPEL_TEST_STRICT_SIBLINGS=1. On CI the extra
is installed; if the install step ever stops installing it, this whole file
skips and the run is green.
"""
import pytest

pytest.importorskip("channels", reason="stapel-notifications[realtime] not installed")
pytest.importorskip(
    "stapel_realtime", reason="stapel-notifications[realtime] not installed"
)

from stapel_realtime import envelope as wire  # noqa: E402


def test_the_frame_reaches_the_socket():
    assert wire is not None
