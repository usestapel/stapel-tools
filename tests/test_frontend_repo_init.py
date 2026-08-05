"""The publishing half of the delivery canon, for a SEPARATE frontend repo.

Nothing in this toolchain ever wrote into a split-repo frontend, so the canon
only existed on the consuming side: the backend declared a volume and a
one-shot service, and the frontend repo had no idea it was supposed to publish
into them. Measured on ironmemo (2026-08-05): `ironmemo-frontend` carries no
Dockerfile and no CI at all — only a locally built `dist/`.
"""
import pytest

from stapel_tools.frontend_repo_init import init_frontend_repo


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "acme-frontend"
    d.mkdir()
    (d / "package.json").write_text('{"name": "acme-frontend"}\n')
    return d


def test_writes_dockerfile_publish_script_and_ci(repo):
    init_frontend_repo(repo, ci="gitlab")
    assert (repo / "Dockerfile").is_file()
    assert (repo / "frontend-publish.sh").is_file()
    assert (repo / ".gitlab-ci.yml").is_file()
    # the publish script must be executable — the Dockerfile chmods it too,
    # but a repo whose script is not executable trips people locally
    assert (repo / "frontend-publish.sh").stat().st_mode & 0o111


def test_ci_pushes_an_immutable_tag_never_latest(repo):
    init_frontend_repo(repo, ci="gitlab")
    ci = (repo / ".gitlab-ci.yml").read_text()
    assert "sha-$CI_COMMIT_SHORT_SHA" in ci
    assert ":latest" not in ci


def test_export_stage_is_a_dist_carrier_not_an_nginx_image(repo):
    """A frontend repo shipping its own nginx would move reserved paths, TLS,
    the proxy table and the cache canon outside the backend repo's gates."""
    init_frontend_repo(repo, ci="none")
    dockerfile = (repo / "Dockerfile").read_text()
    assert "FROM build AS export" in dockerfile
    froms = [ln for ln in dockerfile.splitlines() if ln.startswith("FROM ")]
    assert froms and not any("nginx" in ln.lower() for ln in froms)
    assert "frontend-publish" in dockerfile


def test_existing_ci_is_not_silently_merged(repo):
    """Appending a job into someone's pipeline by string surgery is how you
    break their pipeline. Print it and let a human place it."""
    (repo / ".gitlab-ci.yml").write_text("stages: [test]\n")
    out = "\n".join(init_frontend_repo(repo, ci="gitlab"))
    assert "NOT merged automatically" in out
    assert (repo / ".gitlab-ci.yml").read_text() == "stages: [test]\n"


def test_existing_files_are_not_clobbered_without_force(repo):
    (repo / "Dockerfile").write_text("FROM scratch\n")
    init_frontend_repo(repo, ci="none")
    assert (repo / "Dockerfile").read_text() == "FROM scratch\n"
    init_frontend_repo(repo, ci="none", force=True)
    assert "FROM node:22-alpine AS build" in (repo / "Dockerfile").read_text()


def test_refuses_a_directory_that_is_not_a_frontend(tmp_path):
    (tmp_path / "notafrontend").mkdir()
    with pytest.raises(SystemExit, match="no package.json"):
        init_frontend_repo(tmp_path / "notafrontend")


def test_install_step_follows_the_lockfile(tmp_path):
    """`npm ci` in a pnpm repo does not fail loudly — it resolves a DIFFERENT
    dependency tree than every developer has, and the image you ship stops
    matching the app anyone tested. ironmemo-frontend is pnpm."""
    for lock, expect in (
        ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
        ("yarn.lock", "yarn install --immutable"),
        (None, "npm ci"),
    ):
        d = tmp_path / (lock or "npm")
        d.mkdir()
        (d / "package.json").write_text("{}\n")
        if lock:
            (d / lock).write_text("")
        init_frontend_repo(d, ci="none")
        assert expect in (d / "Dockerfile").read_text()
