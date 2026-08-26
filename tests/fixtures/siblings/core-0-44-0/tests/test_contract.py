"""Excerpt of stapel-core tests/test_contract.py (unchanged at HEAD).

The contract gate drives stapel-tools, which nothing in stapel-core's
pyproject declares — nine import sites in the real file, two here. It has
never reddened a run only because the workflow installs stapel-tools by hand
for the docs job; the declaration still does not exist.
"""
REPO = "."


def test_llms_txt_has_no_drift():
    from stapel_tools.llms_txt import render

    assert render


def test_capabilities_has_no_drift():
    from stapel_tools.capabilities import build_capabilities

    assert build_capabilities
