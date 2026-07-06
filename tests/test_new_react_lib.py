"""stapel-new-react-lib scaffold tests: context naming, file plan inventory,
rendering hygiene, gen-script wiring, and structural snapshot."""
import json

from stapel_tools.new_react_lib import build_context, file_plan, scaffold_react_lib

REQUIRED = [
    "package.json",
    "tsconfig.json",
    "vitest.config.ts",
    "README.md",
    "MODULE.md",
    "CHANGELOG.md",
    "src/index.ts",
    "src/api/types.ts",
    "src/api/extensions.ts",
    "src/model/queryKeys.ts",
    "src/model/runtime.ts",
    "src/model/context.tsx",
    "src/flows/errors.ts",
    "src/i18n/keys.ts",
    "src/i18n/errorsMap.ts",
    "test/pair.test.ts",
]

# Layers the checklist (frontend-core-architecture §4) requires per pair.
LAYER_DIRS = ["src/api", "src/model", "src/flows", "src/headless", "src/i18n"]

TOKENS = (
    "{{MODULE}}", "{{CAMEL}}", "{{UPPER}}", "{{PKG_DIR}}", "{{PKG_NAME}}",
    "{{BACKEND}}", "{{PATH_PREFIX}}", "{{ERRORS_SOURCE}}", "{{TITLE}}",
    "{{DESC}}", "{{YEAR}}",
)


class TestContext:
    def test_naming(self):
        ctx = build_context("notifications", "Notifications", "stapel-notifications",
                            "/notifications/api/")
        assert ctx["MODULE"] == "notifications"
        assert ctx["CAMEL"] == "Notifications"
        assert ctx["UPPER"] == "NOTIFICATIONS"
        assert ctx["PKG_NAME"] == "@stapel/notifications-react"
        assert ctx["PKG_DIR"] == "notifications-react"
        assert ctx["ERRORS_SOURCE"] == "../stapel-notifications/errors.py"


class TestFilePlan:
    def _plan(self, module="notifications"):
        ctx = build_context(module, module.capitalize(), f"stapel-{module}",
                            f"/{module}/api/")
        return ctx, file_plan(ctx)

    def test_full_inventory(self):
        _, plan = self._plan()
        for rel in REQUIRED:
            assert rel in plan, rel
        # module-named files
        assert "src/api/notificationsApi.ts" in plan
        assert "src/headless/NotificationsProvider.tsx" in plan

    def test_all_layers_present(self):
        _, plan = self._plan()
        for layer in LAYER_DIRS:
            assert any(rel.startswith(layer + "/") for rel in plan), layer

    def test_no_unrendered_placeholders(self):
        for module in ("notifications", "billing", "profiles", "workspaces"):
            ctx = build_context(module, module.capitalize(), f"stapel-{module}",
                                f"/{module}/api/")
            for rel, content in file_plan(ctx).items():
                for token in TOKENS:
                    assert token not in content, f"{module}:{rel} leaks {token}"

    def test_package_json_wires_shared_gen_drivers(self):
        _, plan = self._plan()
        pkg = json.loads(plan["package.json"])
        assert pkg["name"] == "@stapel/notifications-react"
        scripts = pkg["scripts"]
        # three per-package drift gates; gen:api is core-owned and absent
        for gate in ("gen:flows:check", "gen:errors:check", "gen:manifest:check"):
            assert gate in scripts, gate
        assert "gen:api" not in scripts
        # env knobs parametrize the SHARED root drivers (fork-free)
        assert "FLOW_MODULE=notifications" in scripts["gen:flows"]
        assert "../../scripts/gen-flows.mjs" in scripts["gen:flows"]
        assert "ERRORS_CONST=NOTIFICATIONS_ERRORS" in scripts["gen:errors"]
        assert "../stapel-notifications/errors.py" in scripts["gen:errors"]
        assert "MANIFEST_MODULE=stapel-notifications" in scripts["gen:manifest"]
        assert "MANIFEST_TAGPREFIX=/notifications/api/" in scripts["gen:manifest"]

    def test_package_hygiene(self):
        _, plan = self._plan()
        pkg = json.loads(plan["package.json"])
        assert pkg["sideEffects"] is False
        assert pkg["type"] == "module"
        assert "src" in pkg["files"]  # sources-in-tarball (§7 eject)
        assert pkg["exports"]["./llms.txt"] == "./llms.txt"
        assert pkg["exports"]["./manifest"] == "./manifest.json"
        assert "size-limit" in pkg
        assert pkg["peerDependencies"]["react"] == ">=19"

    def test_primitive_imported_not_copied(self):
        """createFlowMachine must come from @stapel/core, never be redefined."""
        _, plan = self._plan()
        errors = plan["src/flows/errors.ts"]
        assert 'from "@stapel/core"' in errors
        index = plan["src/index.ts"]
        assert 'export { createFlowMachine, useFlow, isErrorCode } from "@stapel/core"' in index
        # no local flow-machine implementation anywhere in the skeleton
        for rel, content in plan.items():
            assert "function createFlowMachine" not in content, rel

    def test_tsconfig_isolated_declarations(self):
        _, plan = self._plan()
        ts = json.loads(plan["tsconfig.json"])
        assert ts["compilerOptions"]["isolatedDeclarations"] is True


class TestScaffold:
    def test_writes_under_packages(self, tmp_path):
        (tmp_path / "packages").mkdir()
        target = scaffold_react_lib("notifications", "Notifications", tmp_path)
        assert target == tmp_path / "packages" / "notifications-react"
        assert (target / "package.json").exists()
        assert (target / "src/index.ts").exists()
        assert (target / "src/api/notificationsApi.ts").exists()
        assert (target / "src/headless/NotificationsProvider.tsx").exists()

    def test_refuses_existing_target(self, tmp_path):
        (tmp_path / "packages").mkdir()
        scaffold_react_lib("notifications", "Notifications", tmp_path)
        import pytest

        with pytest.raises(SystemExit):
            scaffold_react_lib("notifications", "Notifications", tmp_path)

    def test_custom_backend_and_prefix(self, tmp_path):
        (tmp_path / "packages").mkdir()
        target = scaffold_react_lib(
            "billing", "Billing", tmp_path,
            backend="stapel-billing", path_prefix="/billing/api/",
        )
        pkg = json.loads((target / "package.json").read_text())
        assert "../stapel-billing/errors.py" in pkg["scripts"]["gen:errors"]
        assert "MANIFEST_TAGPREFIX=/billing/api/" in pkg["scripts"]["gen:manifest"]
