"""Tests for the build/disk lifecycle mechanism (``stapel_tools.disk_guard``).

Three things are load-bearing and each has its own class here:

1. the **preflight guard** refuses below the threshold, passes above it, and the
   refusal names the free space, the threshold and the reclaim command;
2. the **tier boundaries** — tier 1 never touches images, tier 2 does, and a
   volume prune is refused at every tier with the reason in the message;
3. the **reaper** — dry-run lists, a real run removes only what identifies
   itself as ephemeral, and everything else is left untouched.

No docker daemon is involved: :class:`FakeDocker` records the argv it is handed.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from stapel_tools import disk_guard as dg


# ── fakes ───────────────────────────────────────────────────────────────────
def _done(stdout: str = "", rc: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


class FakeDocker(dg.Docker):
    """A Docker seam backed by canned tables; records every argv."""

    def __init__(self, *, containers=(), volumes=(), networks=(), images=(), df_rows=()):
        self.calls: list[list[str]] = []
        self.removed: list[str] = []
        self._tables = {
            "ps": containers, "volume ls": volumes,
            "network ls": networks, "image ls": images,
        }
        self._df_rows = df_rows
        super().__init__(runner=self._runner)

    def _runner(self, argv):
        argv = list(argv)[1:]  # drop "docker"
        self.calls.append(argv)
        joined = " ".join(argv)
        if joined.startswith("version"):
            return _done("29.4.0\n")
        if joined.startswith("system df"):
            return _done("\n".join(json.dumps(r) for r in self._df_rows))
        for key, rows in self._tables.items():
            if joined.startswith(key):
                return _done("\n".join("\t".join(r) for r in rows))
        if argv[:2] == ["rm", "-f"] or argv[:2] == ["volume", "rm"] \
                or argv[:2] == ["network", "rm"] or argv[:2] == ["image", "rm"]:
            self.removed.append(argv[-1])
            return _done("removed\n")
        return _done("")


def usage(free_gib: float, total_gib: float = 460.0):
    total = int(total_gib * dg.GIB)
    free = int(free_gib * dg.GIB)
    return lambda _path: (total, total - free, free)


# ── 1. preflight guard ──────────────────────────────────────────────────────
class TestGuard:
    def test_refuses_below_threshold(self):
        result = dg.check_free_space("/", threshold=15 * dg.GIB, usage=usage(3.0))
        assert result.ok is False
        assert result.shortfall == 12 * dg.GIB

    def test_passes_above_threshold(self):
        result = dg.check_free_space("/", threshold=15 * dg.GIB, usage=usage(33.0))
        assert result.ok is True
        assert result.shortfall == 0

    def test_boundary_is_inclusive(self):
        assert dg.check_free_space("/", threshold=15 * dg.GIB, usage=usage(15.0)).ok

    def test_refusal_message_names_the_numbers_and_the_command(self):
        result = dg.check_free_space("/", threshold=15 * dg.GIB, usage=usage(3.0),
                                     target="make e2e")
        msg = result.message()
        assert "3.0 GiB" in msg          # what is free
        assert "15.0 GiB" in msg         # what is required
        assert "12.0 GiB" in msg         # the shortfall
        assert dg.RECLAIM_COMMAND in msg  # the exact reclaim command
        assert "make e2e" in msg          # which target refused
        assert "ENOSPC" in msg            # why refusing beats dying mid-build
        assert "never blanket-pruned" in msg

    def test_threshold_precedence_flag_then_env_then_default(self):
        assert dg.min_free_bytes(2.0, env={dg.MIN_FREE_ENV: "9"}) == int(2 * dg.GIB)
        assert dg.min_free_bytes(None, env={dg.MIN_FREE_ENV: "9"}) == int(9 * dg.GIB)
        assert dg.min_free_bytes(None, env={}) == int(dg.DEFAULT_MIN_FREE_GB * dg.GIB)
        # a junk env value must not crash a build gate
        assert dg.min_free_bytes(None, env={dg.MIN_FREE_ENV: "lots"}) \
            == int(dg.DEFAULT_MIN_FREE_GB * dg.GIB)

    def test_cli_exit_codes(self, monkeypatch, capsys):
        monkeypatch.setattr(dg.shutil, "disk_usage", usage(1.0))
        assert dg.main(["guard", "--min-free-gb", "15"], docker=FakeDocker()) == 1
        assert "DISK GUARD REFUSED" in capsys.readouterr().err
        monkeypatch.setattr(dg.shutil, "disk_usage", usage(99.0))
        assert dg.main(["guard", "--min-free-gb", "15"], docker=FakeDocker()) == 0
        assert "disk guard OK" in capsys.readouterr().out


# ── 2. tier boundaries ──────────────────────────────────────────────────────
class TestTiers:
    def test_tier1_touches_cache_containers_and_dangling_images_only(self):
        dock = FakeDocker()
        steps, refusal = dg.reclaim(dock, images=False)
        assert refusal is None
        argvs = [" ".join(s.argv) for s in steps]
        assert argvs == ["builder prune -f", "container prune -f", "image prune -f"]
        assert all(s.tier == 1 for s in steps)
        # tier 1 must never reach an unreferenced (but tagged) image
        assert "image prune -a -f" not in argvs

    def test_tier2_is_opt_in(self):
        steps, _ = dg.reclaim(FakeDocker(), images=True)
        tier2 = [s for s in steps if s.tier == 2]
        assert [" ".join(s.argv) for s in tier2] == ["image prune -a -f"]

    def test_volumes_are_refused_at_every_tier(self):
        for images in (False, True):
            steps, refusal = dg.reclaim(FakeDocker(), images=images, volumes=True)
            assert steps == []
            assert refusal is not None
            assert "REFUSED" in refusal
            assert "project databases" in refusal
            assert "stapel-disk reap" in refusal

    def test_volume_refusal_runs_nothing_at_all(self):
        dock = FakeDocker()
        dg.reclaim(dock, images=True, volumes=True)
        assert dock.calls == []

    def test_no_reclaim_command_ever_carries_a_volume_flag(self):
        """The hard invariant: no argv this module emits can delete a volume."""
        for images in (False, True):
            steps, _ = dg.reclaim(FakeDocker(), images=images, dry_run=True)
            for step in steps:
                joined = " ".join(step.argv)
                assert "--volumes" not in joined
                assert not joined.startswith("volume prune")
                assert not joined.startswith("system prune")

    def test_cli_volume_flag_exits_two_with_the_reason(self, capsys):
        assert dg.main(["reclaim", "--volumes"], docker=FakeDocker()) == 2
        err = capsys.readouterr().err
        assert "never blanket-prunes volumes" in err
        assert "project repositories" in err


# ── 3. the reaper ───────────────────────────────────────────────────────────
EPH = f"{dg.EPHEMERAL_LABEL}={dg.EPHEMERAL_LABEL_VALUE}"


def fleet_docker():
    """A fake engine holding the real mix: durable data next to e2e garbage."""
    return FakeDocker(
        containers=[
            ["c1", "studio-sandbox-abc123def456", ""],           # pattern
            ["c2", "studio-quiet-otter-4417", EPH],              # label
            ["c3", "stapel-studio-local-web-1", "com.docker.compose.project=x"],  # durable
            ["c4", "ironmemo-backend-kafka-1", ""],              # durable
        ],
        volumes=[
            ["studio-vol-e2e-2f3f7a8c", ""],                     # pattern
            ["studio-vol-quiet-otter-4417", EPH],                # label
            ["studio-vol-client", ""],                            # DURABLE project repo
            ["stapel-studio-local_project-repos", ""],           # DURABLE
            ["stapel-studio-local_db-data", ""],                 # DURABLE
            ["ironmemo-backend_postgres_data", ""],              # DURABLE
        ],
        networks=[
            ["n1", "studio-net-e2e-2f3f7a8c", ""],               # pattern
            ["n2", "studio-net-client", ""],                      # durable
            ["n3", "bridge", ""],
        ],
        images=[
            ["i1", "studio-e2e-scratch:local", "", "120MB"],     # pattern
            ["i2", "stapel-studio:local", "", "1.47GB"],         # durable
        ],
    )


class TestReaper:
    def test_dry_run_lists_and_removes_nothing(self):
        dock = fleet_docker()
        report = dg.reap(dock, dry_run=True)
        assert dock.removed == []
        names = sorted(r.name for r in report.matched)
        assert names == [
            "studio-e2e-scratch:local",
            "studio-net-e2e-2f3f7a8c",
            "studio-quiet-otter-4417",
            "studio-sandbox-abc123def456",
            "studio-vol-e2e-2f3f7a8c",
            "studio-vol-quiet-otter-4417",
        ]

    def test_real_run_removes_only_matching(self):
        dock = fleet_docker()
        report = dg.reap(dock)
        assert len(report.removed) == 6
        assert report.failed == []

    def test_durable_resources_are_untouched(self):
        dock = fleet_docker()
        dg.reap(dock)
        for durable in ("studio-vol-client", "stapel-studio-local_project-repos",
                        "stapel-studio-local_db-data", "ironmemo-backend_postgres_data",
                        "studio-net-client", "bridge", "stapel-studio:local"):
            assert durable not in dock.removed
        # …and they were inspected, not merely missed: 2 containers + 4 volumes
        # + 2 networks + 1 image were looked at and deliberately left alone.
        assert dg.reap(fleet_docker(), dry_run=True).skipped == 9

    def test_containers_are_removed_before_their_volumes(self):
        """A volume still attached to a live container cannot be removed."""
        dock = fleet_docker()
        dg.reap(dock)
        kinds = [c[0] for c in dock.calls if c[:2] in (["rm", "-f"], ["volume", "rm"],
                                                       ["network", "rm"], ["image", "rm"])]
        assert kinds.index("rm") < kinds.index("volume")
        assert kinds.index("volume") < kinds.index("network")

    def test_container_removal_takes_its_anonymous_volumes_with_it(self):
        """`docker rm -f` without -v is the bug that orphaned 131 PGDATA volumes."""
        dock = fleet_docker()
        dg.reap(dock, kinds=["container"])
        rm_calls = [c for c in dock.calls if c[0] == "rm"]
        assert rm_calls, "nothing was removed"
        assert all("-v" in c for c in rm_calls)

    def test_owner_scope_only_reaps_its_own_label(self):
        dock = FakeDocker(volumes=[
            ["studio-vol-a", f"{EPH},{dg.EPHEMERAL_OWNER_LABEL}=studio-e2e"],
            ["studio-vol-b", f"{EPH},{dg.EPHEMERAL_OWNER_LABEL}=import-e2e"],
            ["studio-vol-e2e-legacy", ""],  # pattern match, but no owner label
        ])
        report = dg.reap(dock, owner="studio-e2e", kinds=["volume"], dry_run=True)
        assert [r.name for r in report.matched] == ["studio-vol-a"]

    @pytest.mark.parametrize("pattern", ["*", "e*", "*e2e*", "std*", ""])
    def test_broad_patterns_are_refused(self, pattern):
        with pytest.raises(dg.UnsafePattern):
            dg.assert_safe_pattern(pattern)

    @pytest.mark.parametrize("pattern", ["studio-vol-e2e-*", "e2e-*", "studio-sandbox-*"])
    def test_specific_patterns_are_accepted(self, pattern):
        assert dg.assert_safe_pattern(pattern) == pattern

    def test_cli_refuses_a_broad_pattern(self, capsys):
        dock = fleet_docker()
        assert dg.main(["reap", "--pattern", "*"], docker=dock) == 2
        assert "too broad" in capsys.readouterr().err
        assert dock.removed == []

    def test_custom_pattern_replaces_defaults(self):
        dock = fleet_docker()
        report = dg.reap(dock, patterns=["studio-net-e2e-*"], kinds=["network"],
                         dry_run=True)
        assert [r.name for r in report.matched] == ["studio-net-e2e-2f3f7a8c"]

    def test_unlabelled_unmatched_resource_is_never_ephemeral(self):
        res = dg.Resource("volume", "v", "stapel-studio-local_project-repos", {})
        assert dg.is_ephemeral(res, dg.DEFAULT_EPHEMERAL_PATTERNS) is False


# ── 4. doctor ───────────────────────────────────────────────────────────────
DF_ROWS = [
    {"Type": "Images", "TotalCount": "99", "Active": "30",
     "Size": "19.84GB", "Reclaimable": "12.39GB (62%)"},
    {"Type": "Containers", "TotalCount": "36", "Active": "36",
     "Size": "133.2MB", "Reclaimable": "0B (0%)"},
    {"Type": "Local Volumes", "TotalCount": "205", "Active": "18",
     "Size": "29.81GB", "Reclaimable": "27.91GB (93%)"},
    {"Type": "Build Cache", "TotalCount": "12", "Active": "0",
     "Size": "5.8GB", "Reclaimable": "5.8GB"},
]


class TestDoctor:
    def _dock(self):
        base = fleet_docker()
        base._df_rows = DF_ROWS
        return base

    def test_reports_space_tiers_and_orphans(self):
        report = dg.doctor(self._dock(), threshold=15 * dg.GIB, usage=usage(3.0))
        assert report["disk"]["ok"] is False
        assert report["tier1"]["reclaimable"] == dg.parse_size("5.8GB")
        assert report["tier2"]["reclaimable"] == dg.parse_size("12.39GB")
        assert report["volumes"]["unreferenced_size"] == dg.parse_size("27.91GB")
        assert report["ephemeral_orphan_total"] == 6
        assert report["ephemeral_orphans"] == {
            "container": 2, "volume": 2, "network": 1, "image": 1,
        }

    def test_volume_line_says_never_pruned(self):
        report = dg.doctor(self._dock(), threshold=15 * dg.GIB, usage=usage(3.0))
        text = dg.format_doctor(report)
        assert "NEVER blanket-pruned" in text
        assert "BELOW THRESHOLD" in text
        assert "stapel-disk reap --dry-run" in text

    def test_json_output_is_machine_readable(self, monkeypatch, capsys):
        monkeypatch.setattr(dg.shutil, "disk_usage", usage(33.0))
        assert dg.main(["doctor", "--json"], docker=self._dock()) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["disk"]["ok"] is True
        assert payload["ephemeral_orphan_total"] == 6


class TestSizeParsing:
    @pytest.mark.parametrize("text,expected", [
        ("0B", 0), ("133.2MB", 133_200_000), ("12.39GB (62%)", 12_390_000_000),
        ("5.8GB", 5_800_000_000), ("", 0), ("nonsense", 0), ("1KiB", 1024),
    ])
    def test_parse_size(self, text, expected):
        assert dg.parse_size(text) == expected
