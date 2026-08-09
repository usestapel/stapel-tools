"""A generated infra line must be STARTABLE, not merely plausible.

Incident (2026-08-07, ironmemo stand): the compose file carried
``command: ["-m", "8222", "--max_payload", "8388608"]``. It looks
reasonable and passes both `docker compose config -q` and any YAML linter.
But nats-server has NO ``--max_payload`` flag: it prints "flag provided but
not defined: -max_payload", prints usage, and exits 0. The container
restart-looped, and everything connecting to it got
``Name or service not known``. A stand failure caught this, not a test or
gate — compose was validated as a document (keys present, YAML parses),
never as a program.

Pins two properties: (1) the generator doesn't emit `max_payload` as a
flag, and (2) the config file a service needs is actually written by the
scaffold — otherwise docker would create a directory in its place and
nats-server would fail to find its config just as silently.

The live check lives in ci.yml's e2e job, which brings nats up for real.
This unit test catches the same class cheaper and earlier.
"""
import tempfile
from pathlib import Path

import pytest
import yaml

from stapel_tools import _compose_templates as ct


class TestGeneratorDoesNotEmitNonexistentFlag:
    def test_max_payload_does_not_reach_the_command_line(self):
        # The incident's exact signature. Checks the PARSED command, not the
        # block's raw text — `--max_payload` legitimately appears in a
        # comment explaining why it must not be a flag. The first version of
        # this test matched on substring and failed on its own comment.
        command = yaml.safe_load(ct.NATS_SERVICE_BLOCK)["nats"]["command"]
        assert not any("max_payload" in arg for arg in command), command

    def test_nats_starts_via_config_file(self):
        block = yaml.safe_load(ct.NATS_SERVICE_BLOCK)["nats"]
        assert block["command"] == ["-c", "/etc/nats/nats.conf"]

    def test_payload_cap_is_declared_in_config_not_dropped(self):
        # Dropping the flag must not mean dropping the setting itself:
        # the 1MiB default is the original file-upload defect.
        assert "max_payload: 8MB" in ct.NATS_CONF

    def test_config_carries_ports_needed_for_healthcheck(self):
        # Compose's healthcheck hits 8222/healthz; the config must open
        # that port or the service comes up permanently unhealthy.
        assert "http_port: 8222" in ct.NATS_CONF
        assert "port: 4222" in ct.NATS_CONF

    def test_jetstream_survives_the_move_off_flags(self):
        # `--jetstream --store_dir /data` were the CORRECT flags; moving to
        # a config file made it easy to lose them along with the wrong one.
        assert "jetstream" in ct.NATS_CONF
        assert "store_dir: /data" in ct.NATS_CONF


class TestMountedFileActuallyExists:
    """A path that's mounted but never created becomes a DIRECTORY under docker."""

    @pytest.fixture
    def project(self):
        from stapel_tools.create_project import create_project

        with tempfile.TemporaryDirectory() as tmp:
            create_project(
                name="natsproj", project_type="monolith", title="Nats",
                url="https://x.dev", company_name="X", company_email="x@x.dev",
                modules=["core"], output_dir=Path(tmp), use_submodules=False,
                init_git=False, broker="nats",
            )
            yield Path(tmp) / "natsproj"

    def test_scaffold_writes_the_config_it_mounts(self, project):
        conf = project / "nats" / "nats.conf"
        assert conf.is_file(), "compose mounts nats/nats.conf — file is missing"
        assert "max_payload" in conf.read_text(encoding="utf-8")

    def test_mount_path_matches_what_was_written(self, project):
        compose = yaml.safe_load(
            (project / "docker-compose.base.yml").read_text(encoding="utf-8")
        )
        mounts = compose["services"]["nats"]["volumes"]
        source = next(m.split(":")[0] for m in mounts if "nats.conf" in m)
        # This exact check catches a path typo that the YAML validator
        # misses and docker turns into an empty directory.
        assert (project / source.lstrip("./")).is_file()
