"""Excerpt of stapel-chat v0.5.0 tests/test_contract.py.

The third undeclared sibling of that release, and the least suspicious one:
the contract gate drives stapel-tools itself.
"""


def test_readme_is_assembled_and_has_no_drift():
    from stapel_tools.readme import load_inputs, render

    assert load_inputs and render
