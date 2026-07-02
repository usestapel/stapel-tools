"""Generator tests — per-primitive broker selection (--task-broker)."""
import pytest

from stapel_tools.create_project import create_project
from stapel_tools.new_service import _detect_transports, scaffold_service


def _create(tmp_path, name, project_type, broker=None, task_broker=None):
    create_project(
        name=name,
        project_type=project_type,
        title=name,
        url="https://x.dev",
        company_name="X",
        company_email="x@x.dev",
        modules=["core"],
        output_dir=tmp_path,
        use_submodules=False,
        init_git=False,
        broker=broker,
        task_broker=task_broker,
    )
    return tmp_path / name


class TestMonolithTaskBroker:
    def test_nats_task_broker_wires_compose_env_settings(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", task_broker="nats")

        compose = (proj / "docker-compose.base.yml").read_text()
        assert "  nats:" in compose
        assert "  nats-data:" in compose
        assert "kafka" not in compose

        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND=nats" in env
        assert "NATS_URL=nats://nats:4222" in env
        assert "STAPEL_TASK_DISPATCH=bus" in env
        assert "routing" not in env

        settings = (proj / "svc-app" / "core" / "settings" / "base.py").read_text()
        # Tasks go to the broker; Actions stay in-process.
        assert '"TASK_DISPATCH": os.getenv("STAPEL_TASK_DISPATCH", "bus")' in settings
        assert '"ACTION_TRANSPORT": os.getenv("STAPEL_ACTION_TRANSPORT", "inprocess")' in settings
        assert '"FUNCTION_TRANSPORT": os.getenv("STAPEL_FUNCTION_TRANSPORT", "inprocess")' in settings

    def test_default_monolith_unchanged(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith")

        compose = (proj / "docker-compose.base.yml").read_text()
        assert "nats" not in compose
        assert "kafka" not in compose

        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND" not in env
        assert "STAPEL_TASK_DISPATCH" not in env

        settings = (proj / "svc-app" / "core" / "settings" / "base.py").read_text()
        assert '"TASK_DISPATCH": os.getenv("STAPEL_TASK_DISPATCH", "action")' in settings

    def test_task_broker_same_as_event_broker_needs_no_routing(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", broker="nats", task_broker="nats")

        compose = (proj / "docker-compose.base.yml").read_text()
        assert compose.count("  nats:") == 1

        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND=nats" in env
        assert "STAPEL_TASK_DISPATCH" not in env
        assert "routing" not in env

        settings = (proj / "svc-app" / "core" / "settings" / "base.py").read_text()
        # Tasks already ride the bus via the Action transport.
        assert '"TASK_DISPATCH": os.getenv("STAPEL_TASK_DISPATCH", "action")' in settings
        assert '"ACTION_TRANSPORT": os.getenv("STAPEL_ACTION_TRANSPORT", "bus")' in settings

    def test_later_services_inherit_task_dispatch_from_env(self, tmp_path):
        proj = _create(tmp_path, "app", "monolith", task_broker="nats")
        assert _detect_transports(proj) == ("inprocess", "inprocess", "bus")

        scaffold_service(
            slug="worker", title="Worker", prefix="svc-", project_root=proj
        )
        settings = (proj / "svc-worker" / "core" / "settings" / "base.py").read_text()
        assert '"TASK_DISPATCH": os.getenv("STAPEL_TASK_DISPATCH", "bus")' in settings
        assert '"ACTION_TRANSPORT": os.getenv("STAPEL_ACTION_TRANSPORT", "inprocess")' in settings


class TestMicroservicesTaskBroker:
    def test_kafka_task_broker_adds_both_brokers_and_routing(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices", task_broker="kafka")

        compose = (proj / "docker-compose.base.yml").read_text()
        assert "  nats:" in compose
        assert "  kafka:" in compose
        assert "  nats-data:" in compose
        assert "  kafka-data:" in compose

        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND=routing" in env
        assert 'STAPEL_BUS_ROUTES={"task.": "kafka", "": "nats"}' in env
        assert "NATS_URL=nats://nats:4222" in env
        assert "KAFKA_BOOTSTRAP_SERVERS=kafka:9092" in env

    def test_compose_yaml_stays_valid(self, tmp_path):
        yaml = pytest.importorskip("yaml")
        proj = _create(tmp_path, "app", "microservices", task_broker="kafka")
        data = yaml.safe_load((proj / "docker-compose.base.yml").read_text())
        assert {"nats", "kafka"} <= set(data["services"])
        assert {"nats-data", "kafka-data"} <= set(data["volumes"])

    def test_default_micro_unchanged(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices")
        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND=nats" in env
        assert "routing" not in env
        assert "kafka" not in (proj / "docker-compose.base.yml").read_text()

    def test_task_broker_same_as_event_broker_needs_no_routing(self, tmp_path):
        proj = _create(
            tmp_path, "app", "microservices", broker="kafka", task_broker="kafka"
        )
        env = (proj / ".env.example").read_text()
        assert "STAPEL_BUS_BACKEND=kafka" in env
        assert "routing" not in env

    def test_later_services_detect_routing_backend(self, tmp_path):
        proj = _create(tmp_path, "app", "microservices", task_broker="kafka")
        # Actions ride the bus; Functions follow the default ("") route.
        assert _detect_transports(proj) == ("bus", "nats", "action")


class TestInvalidCombos:
    def test_minimal_rejects_task_broker(self, tmp_path):
        with pytest.raises(SystemExit):
            _create(tmp_path, "app", "minimal", task_broker="nats")

    def test_minimal_rejects_kafka_task_broker(self, tmp_path):
        with pytest.raises(SystemExit):
            _create(tmp_path, "app", "minimal", task_broker="kafka")

    def test_monolith_rejects_kafka_task_broker(self, tmp_path):
        with pytest.raises(SystemExit):
            _create(tmp_path, "app", "monolith", task_broker="kafka")

    def test_minimal_allows_explicit_none(self, tmp_path):
        proj = _create(tmp_path, "app", "minimal", task_broker="none")
        assert (proj / "manage.py").exists()
