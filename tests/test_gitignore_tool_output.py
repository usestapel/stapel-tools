"""The generated project's root .gitignore keeps frontend tool output out."""
from stapel_tools._compose_templates import MONOLITH_GITIGNORE


def test_root_gitignore_ignores_frontend_tool_output():
    lines = {ln.strip() for ln in MONOLITH_GITIGNORE.splitlines()}
    assert {"node_modules/", ".pnpm-store/", "*.tsbuildinfo"} <= lines
