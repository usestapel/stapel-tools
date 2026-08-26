"""The same excerpt at stapel-chat HEAD: stapel-tools is declared."""


def test_readme_is_assembled_and_has_no_drift():
    from stapel_tools.readme import load_inputs, render

    assert load_inputs and render
