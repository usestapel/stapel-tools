"""stapel-new-react-lib scaffold tests: context naming, file plan inventory,
rendering hygiene, etalon alignment (no per-pair gen scripts, demo layer, test
family, core peer floor), and the idempotent root gen:* wiring (delta 7)."""
import json

from stapel_tools.new_react_lib import (
    build_context,
    core_peer_range,
    file_plan,
    patch_root_gen,
    root_gen_invocations,
    scaffold_react_lib,
)

REQUIRED = [
    "package.json",
    "tsconfig.json",
    "tsconfig.demo.json",
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
    "demo/_harness.tsx",
    "test/pair.test.ts",
    "test/errorsBundle.test.ts",
    "test/flowsContract.test.ts",
    "test/demos.test.tsx",
    "test/prodBundlePurity.test.ts",
]

# Layers the checklist (frontend-core-architecture §4) requires per pair.
LAYER_DIRS = ["src/api", "src/model", "src/flows", "src/headless", "src/i18n"]

TOKENS = (
    "{{MODULE}}", "{{CAMEL}}", "{{UPPER}}", "{{PKG_DIR}}", "{{PKG_NAME}}",
    "{{BACKEND}}", "{{PATH_PREFIX}}", "{{ERRORS_SOURCE}}", "{{TITLE}}",
    "{{DESC}}", "{{CORE_PEER}}", "{{YEAR}}",
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
        # gen-errors reads the backend's docs/errors.json artifact, not errors.py.
        assert ctx["ERRORS_SOURCE"] == "../stapel-notifications/docs/errors.json"
        # default core peer floor (no react-dir wired)
        assert ctx["CORE_PEER"] == ">=0.2.0 <1.0.0"


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
        # starter demo covers the starter headless export (completeness gate)
        assert "demo/Notifications.demo.tsx" in plan

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

    def test_pair_owns_no_gen_scripts(self):
        """Etalon alignment: gen:* lives at the monorepo ROOT (auth-react/tokens
        own NO gen scripts). The pair package.json only has build/test/lint/size."""
        _, plan = self._plan()
        pkg = json.loads(plan["package.json"])
        scripts = pkg["scripts"]
        assert set(scripts) == {"build", "test", "lint", "size"}
        assert not any(k.startswith("gen") for k in scripts)
        # the test task type-checks demos then runs vitest (§4.2)
        assert scripts["test"] == "tsc -p tsconfig.demo.json && vitest run"

    def test_package_hygiene(self):
        _, plan = self._plan()
        pkg = json.loads(plan["package.json"])
        assert pkg["sideEffects"] is False
        assert pkg["type"] == "module"
        assert "src" in pkg["files"]  # sources-in-tarball (§7 eject)
        # demos are first-class code but NEVER shipped (§5.1)
        assert "demo" not in pkg["files"]
        assert pkg["exports"]["./llms.txt"] == "./llms.txt"
        assert pkg["exports"]["./manifest"] == "./manifest.json"
        assert "size-limit" in pkg
        assert pkg["peerDependencies"]["react"] == ">=19"

    def test_core_peer_floor_not_workspace(self):
        """Delta 6: core peer is a pinned floor `>=X.Y.0 <1.0.0` (stops the
        changeset peer-cascade force-major), while the local devDep is workspace:^."""
        _, plan = self._plan()
        pkg = json.loads(plan["package.json"])
        assert pkg["peerDependencies"]["@stapel/core"] == ">=0.2.0 <1.0.0"
        assert pkg["devDependencies"]["@stapel/core"] == "workspace:^"

    def test_showcase_and_tokens_are_dev_only(self):
        """Delta 3: @stapel/showcase is a devDependency (demo authoring); it and
        @stapel/tokens must never be runtime/peer deps."""
        _, plan = self._plan()
        pkg = json.loads(plan["package.json"])
        assert pkg["devDependencies"]["@stapel/showcase"] == "workspace:^"
        assert pkg["devDependencies"]["@stapel/tokens"] == "workspace:^"
        runtime = {**pkg.get("dependencies", {}), **pkg["peerDependencies"]}
        assert "@stapel/showcase" not in runtime

    def test_starter_demo_covers_starter_headless(self):
        """The completeness gate requires ≥1 demo per exported headless component;
        the scaffold's only headless export is <Camel>Provider, and the starter
        demo declares it as `component`."""
        _, plan = self._plan()
        demo = plan["demo/Notifications.demo.tsx"]
        assert "defineDemo(" in demo
        assert "component: NotificationsProvider" in demo
        assert 'from "@stapel/showcase"' in demo
        # index.ts exports exactly that headless component name
        assert "NotificationsProvider" in plan["src/index.ts"]

    def test_harness_is_token_and_i18n_driven(self):
        """The demo harness obeys the product ruleset: colours via cssVar(), labels
        via t(), the tracked <button> carries data-analytics."""
        _, plan = self._plan()
        harness = plan["demo/_harness.tsx"]
        assert "cssVar(" in harness
        assert 'from "@stapel/tokens"' in harness
        assert 'data-analytics="flow"' in harness
        assert "NotificationsDemoHarness" in harness
        assert "createNotificationsRuntime" in harness

    def test_prod_bundle_purity_uses_npm_pack(self):
        """Delta 5: the purity test proves it against the real packed tarball."""
        _, plan = self._plan()
        purity = plan["test/prodBundlePurity.test.ts"]
        assert 'execFileSync("npm", ["pack", "--dry-run", "--json"]' in purity

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
        # the demo tsconfig type-checks demos with noEmit (never shipped)
        demo_ts = json.loads(plan["tsconfig.demo.json"])
        assert demo_ts["compilerOptions"]["noEmit"] is True
        assert "demo" in demo_ts["include"]
        assert demo_ts["exclude"] == ["demo/generated"]


class TestCorePeerRange:
    def test_reads_current_core_minor(self, tmp_path):
        core = tmp_path / "packages" / "core"
        core.mkdir(parents=True)
        (core / "package.json").write_text(json.dumps({"version": "0.5.3"}))
        assert core_peer_range(tmp_path) == ">=0.5.0 <1.0.0"

    def test_falls_back_when_core_absent(self, tmp_path):
        assert core_peer_range(tmp_path) == ">=0.2.0 <1.0.0"


# A minimal root package.json mirroring the etalon's root gen:* shape.
def _root_scripts():
    return {
        "gen:flows": "node scripts/gen-flows.mjs",
        "gen:flows:check": "node scripts/gen-flows.mjs && git diff --exit-code -- packages/auth-react/src/flows/generated",
        "gen:errors": "node scripts/gen-errors.mjs",
        "gen:errors:check": "node scripts/gen-errors.mjs && git diff --exit-code -- packages/auth-react/src/i18n/generated",
        "gen:events": "node scripts/gen-events.mjs",
        "gen:events:check": "node scripts/gen-events.mjs && git diff --exit-code -- packages/auth-react/src/analytics/generated/events.json",
        "gen:demos": "node scripts/gen-demos.mjs",
        "gen:demos:check": "pnpm gen:demos && git diff --exit-code -- packages/auth-react/demo/generated",
        "gen:manifest": "node scripts/gen-manifest.mjs",
        "gen:manifest:check": "node scripts/gen-manifest.mjs && git diff --exit-code -- packages/auth-react/manifest.json packages/auth-react/llms.txt",
    }


def _write_root(tmp_path, scripts):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "stapel-react", "scripts": scripts}, indent=2) + "\n"
    )


class TestRootGenPatch:
    def _ctx(self):
        return build_context("notifications", "Notifications",
                             "stapel-notifications", "/notifications/api/")

    def test_enumerates_pair_in_every_driver(self, tmp_path):
        _write_root(tmp_path, _root_scripts())
        ok, changed = patch_root_gen(tmp_path, self._ctx())
        assert ok
        scripts = json.loads((tmp_path / "package.json").read_text())["scripts"]
        # each driver's :check now diffs the pair's generated path
        for name in ("flows", "errors", "events", "demos", "manifest"):
            assert "packages/notifications-react" in scripts[f"gen:{name}:check"], name
        # correct env knobs are wired (fork-free — shared drivers)
        assert "FLOW_MODULE=notifications" in scripts["gen:flows"]
        assert "AUTH_ERRORS_JSON=../stapel-notifications/docs/errors.json" in scripts["gen:errors"]
        assert "ERRORS_CONST=NOTIFICATIONS_ERRORS" in scripts["gen:errors"]
        assert "EVENTS_PKG_DIR=packages/notifications-react" in scripts["gen:events"]
        assert "DEMOS_PKG_DIR=packages/notifications-react" in scripts["gen:demos"]
        assert "MANIFEST_MODULE=stapel-notifications" in scripts["gen:manifest"]
        assert "MANIFEST_BACKEND_PYPROJECT=../stapel-notifications/pyproject.toml" in scripts["gen:manifest"]

    def test_inline_check_reruns_gen_but_demos_does_not_double(self, tmp_path):
        _write_root(tmp_path, _root_scripts())
        patch_root_gen(tmp_path, self._ctx())
        scripts = json.loads((tmp_path / "package.json").read_text())["scripts"]
        # inline checks (flows) re-run the driver AND diff the new path
        assert scripts["gen:flows:check"].count("FLOW_MODULE=notifications") == 1
        assert "packages/notifications-react/src/flows/generated" in scripts["gen:flows:check"]
        # demos:check delegates to `pnpm gen:demos` (already patched) → no second
        # gen invocation, only the extra diff path
        assert "DEMOS_PKG_DIR=packages/notifications-react" not in scripts["gen:demos:check"]
        assert "packages/notifications-react/demo/generated" in scripts["gen:demos:check"]

    def test_idempotent(self, tmp_path):
        _write_root(tmp_path, _root_scripts())
        patch_root_gen(tmp_path, self._ctx())
        first = (tmp_path / "package.json").read_text()
        ok, changed = patch_root_gen(tmp_path, self._ctx())
        assert ok
        assert changed == []
        assert (tmp_path / "package.json").read_text() == first

    def test_falls_back_on_unexpected_shape(self, tmp_path):
        # missing gen:events → cannot safely patch
        scripts = _root_scripts()
        del scripts["gen:events"]
        del scripts["gen:events:check"]
        _write_root(tmp_path, scripts)
        ok, changed = patch_root_gen(tmp_path, self._ctx())
        assert ok is False
        assert changed == []

    def test_missing_root_returns_false(self, tmp_path):
        ok, changed = patch_root_gen(tmp_path, self._ctx())
        assert ok is False


class TestScaffold:
    def _react(self, tmp_path):
        (tmp_path / "packages").mkdir()
        _write_root(tmp_path, _root_scripts())
        core = tmp_path / "packages" / "core"
        core.mkdir()
        (core / "package.json").write_text(json.dumps({"version": "0.4.1"}))
        return tmp_path

    def test_writes_under_packages(self, tmp_path):
        react = self._react(tmp_path)
        target = scaffold_react_lib("notifications", "Notifications", react)
        assert target == react / "packages" / "notifications-react"
        assert (target / "package.json").exists()
        assert (target / "src/index.ts").exists()
        assert (target / "src/api/notificationsApi.ts").exists()
        assert (target / "src/headless/NotificationsProvider.tsx").exists()
        assert (target / "demo/_harness.tsx").exists()
        assert (target / "demo/Notifications.demo.tsx").exists()
        assert (target / "test/prodBundlePurity.test.ts").exists()
        # core peer floor derived from the monorepo core version at gen time
        pkg = json.loads((target / "package.json").read_text())
        assert pkg["peerDependencies"]["@stapel/core"] == ">=0.4.0 <1.0.0"
        # the root was enumerated with the pair
        root = json.loads((react / "package.json").read_text())["scripts"]
        assert "FLOW_MODULE=notifications" in root["gen:flows"]
        assert "packages/notifications-react/demo/generated" in root["gen:demos:check"]

    def test_refuses_existing_target(self, tmp_path):
        react = self._react(tmp_path)
        scaffold_react_lib("notifications", "Notifications", react)
        import pytest

        with pytest.raises(SystemExit):
            scaffold_react_lib("notifications", "Notifications", react)

    def test_custom_backend_and_prefix(self, tmp_path):
        react = self._react(tmp_path)
        scaffold_react_lib(
            "billing", "Billing", react,
            backend="stapel-billing", path_prefix="/billing/api/",
        )
        root = json.loads((react / "package.json").read_text())["scripts"]
        assert "../stapel-billing/docs/errors.json" in root["gen:errors"]
        assert "MANIFEST_TAGPREFIX=/billing/api/" in root["gen:manifest"]


class TestRootGenInvocations:
    def test_covers_all_five_drivers(self):
        ctx = build_context("notifications", "Notifications",
                            "stapel-notifications", "/notifications/api/")
        names = {d["name"] for d in root_gen_invocations(ctx)}
        assert names == {"flows", "errors", "events", "demos", "manifest"}
