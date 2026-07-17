"""Owner decision, §57 revision — generated deploy/ scripts with the hard
gate against deploying a default/dev env. The committed .env.local is safe
exactly because this gate (and stapel-core's prodguard at boot) refuses it."""
import stat
import subprocess

import pytest

from stapel_tools.create_project import create_project

GOOD_ENV = """\
SECRET_KEY=k2J8vLq0N7xTzR4mW6yB1cD3eF5gH9iA0sPdUoQwErTyUiOpAsDfGh
JWT_SECRET_KEY=q1W2e3R4t5Y6u7I8o9P0aSdFgHjKlZxCvBnM1234567890abcdefgh
DEBUG=false
DJANGO_ENV=prod
POSTGRES_PASSWORD=W8mK2pX9vL4qR7tZ3nB6yC1d
DJANGO_SUPERUSER_PASSWORD=H4sMxPz82RkWq7Lt
"""


def _create(tmp_path, ptype="monolith", name="app"):
    create_project(
        name=name, project_type=ptype, title=name.capitalize(),
        url="https://x.dev", company_name="X", company_email="x@x.dev",
        modules=["core"], output_dir=tmp_path,
        use_submodules=False, init_git=False,
    )
    return tmp_path / name


def _run_gate(proj, env_file):
    return subprocess.run(
        ["sh", "deploy/check-env.sh", env_file],
        cwd=proj, capture_output=True, text=True,
    )


class TestDeployScriptsEmitted:
    @pytest.mark.parametrize("ptype", ["monolith", "microservices"])
    def test_deploy_dir_with_executable_scripts(self, tmp_path, ptype):
        proj = _create(tmp_path, ptype)
        for rel in ("deploy/deploy.sh", "deploy/check-env.sh"):
            path = proj / rel
            assert path.exists(), rel
            assert path.stat().st_mode & stat.S_IXUSR, f"{rel} not executable"

    def test_minimal_has_no_deploy_dir(self, tmp_path):
        # minimal ships no docker/prod compose — nothing to deploy with these.
        proj = _create(tmp_path, "minimal")
        assert not (proj / "deploy").exists()

    def test_deploy_sh_targets_prod_compose_and_calls_gate(self, tmp_path):
        proj = _create(tmp_path)
        text = (proj / "deploy" / "deploy.sh").read_text()
        assert "check-env.sh" in text
        assert "-f docker-compose.yml" in text
        assert "docker-compose.local.yml" not in text.replace(
            "never docker-compose.local.yml", ""
        ).replace("# ", "")  # only ever mentioned as forbidden, in comments


class TestCheckEnvGate:
    def test_refuses_the_committed_env_local(self, tmp_path):
        """The whole point: the committed local env must never deploy."""
        proj = _create(tmp_path)
        result = _run_gate(proj, ".env.local")
        assert result.returncode == 1
        assert "STAPEL_LOCAL_ENV" in result.stderr
        assert "запрещён" in result.stderr or "REFUSING" in result.stderr

    def test_refuses_dev_marked_secret_key(self, tmp_path):
        proj = _create(tmp_path)
        env = proj / ".env.stage"
        env.write_text(GOOD_ENV.replace(
            "SECRET_KEY=k2J8vLq0N7xTzR4mW6yB1cD3eF5gH9iA0sPdUoQwErTyUiOpAsDfGh",
            "SECRET_KEY=django-insecure-dev-app-committed-local-only-never-deploy",
        ))
        result = _run_gate(proj, ".env.stage")
        assert result.returncode == 1
        assert "SECRET_KEY" in result.stderr

    def test_refuses_dev_insecure_prefix_too(self, tmp_path):
        # The owner-sketched marker spelling is refused as well.
        proj = _create(tmp_path)
        env = proj / ".env.stage"
        env.write_text(GOOD_ENV.replace(
            "SECRET_KEY=k2J8vLq0N7xTzR4mW6yB1cD3eF5gH9iA0sPdUoQwErTyUiOpAsDfGh",
            "SECRET_KEY=dev-insecure-whatever-somebody-hand-rolled",
        ))
        assert _run_gate(proj, ".env.stage").returncode == 1

    def test_refuses_debug_true(self, tmp_path):
        proj = _create(tmp_path)
        (proj / ".env.stage").write_text(GOOD_ENV.replace("DEBUG=false", "DEBUG=true"))
        result = _run_gate(proj, ".env.stage")
        assert result.returncode == 1
        assert "DEBUG" in result.stderr

    def test_refuses_default_postgres_password(self, tmp_path):
        proj = _create(tmp_path)
        (proj / ".env.stage").write_text(GOOD_ENV.replace(
            "POSTGRES_PASSWORD=W8mK2pX9vL4qR7tZ3nB6yC1d", "POSTGRES_PASSWORD=stapel"
        ))
        result = _run_gate(proj, ".env.stage")
        assert result.returncode == 1
        assert "POSTGRES_PASSWORD" in result.stderr

    def test_refuses_admin_superuser_password(self, tmp_path):
        proj = _create(tmp_path)
        (proj / ".env.stage").write_text(GOOD_ENV.replace(
            "DJANGO_SUPERUSER_PASSWORD=H4sMxPz82RkWq7Lt",
            "DJANGO_SUPERUSER_PASSWORD=admin",
        ))
        result = _run_gate(proj, ".env.stage")
        assert result.returncode == 1
        assert "DJANGO_SUPERUSER_PASSWORD" in result.stderr

    def test_refuses_explicit_mock_providers(self, tmp_path):
        proj = _create(tmp_path)
        (proj / ".env.stage").write_text(GOOD_ENV + "EMAIL_PROVIDER=mock\n")
        result = _run_gate(proj, ".env.stage")
        assert result.returncode == 1
        assert "EMAIL_PROVIDER" in result.stderr

    def test_refuses_non_prod_django_env(self, tmp_path):
        proj = _create(tmp_path)
        (proj / ".env.stage").write_text(GOOD_ENV.replace(
            "DJANGO_ENV=prod", "DJANGO_ENV=local"
        ))
        result = _run_gate(proj, ".env.stage")
        assert result.returncode == 1
        assert "DJANGO_ENV" in result.stderr

    def test_refuses_missing_file(self, tmp_path):
        proj = _create(tmp_path)
        assert _run_gate(proj, ".env.nothere").returncode == 1

    def test_accepts_a_real_stand_env(self, tmp_path):
        proj = _create(tmp_path)
        (proj / ".env.stage").write_text(GOOD_ENV)
        result = _run_gate(proj, ".env.stage")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "deployable" in result.stdout
