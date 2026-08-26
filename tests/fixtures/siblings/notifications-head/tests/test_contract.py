"""Excerpt of stapel-notifications HEAD tests/test_contract.py — stapel-tools
is imported here and declared in the `test` extra, which is what keeps SIB005
quiet about it."""


def test_llms_txt_has_no_drift():
    from stapel_tools.llms_txt import render

    assert render
