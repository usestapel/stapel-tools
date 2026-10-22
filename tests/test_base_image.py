"""--base-image / $STAPEL_BASE_IMAGE: the FROM of every generated Python service."""
import re

import pytest

from stapel_tools import image_lint
from stapel_tools.create_project import apply_base_image, create_project, resolve_base_image

REF = "registry.example/python-base:2026-01-01-abcdef12"


def _gen(tmp_path, name, project_type="monolith", modules=None, **kw):
    create_project(
        name=name, project_type=project_type, title=name, url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules or ["core"],
        output_dir=tmp_path, use_submodules=False, init_git=False, **kw,
    )
    return tmp_path / name


def _py_dockerfiles(proj):
    return [p for p in proj.rglob("Dockerfile")
            if "node_modules" not in p.parts and re.search(r"^FROM (python:|registry\.)", p.read_text(), re.M)]


def _froms(path):
    return re.findall(r"^FROM (\S+)", path.read_text(), re.M)


def test_default_from_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("STAPEL_BASE_IMAGE", raising=False)
    proj = _gen(tmp_path, "plain")
    files = _py_dockerfiles(proj)
    assert files
    for f in files:
        assert _froms(f) == ["python:3.12-slim"]
        assert "USER" not in f.read_text()


def test_flag_sets_from_on_every_service(tmp_path, monkeypatch):
    monkeypatch.delenv("STAPEL_BASE_IMAGE", raising=False)
    proj = _gen(tmp_path, "svcs", modules=["core", "cdn"], base_image=REF)
    files = _py_dockerfiles(proj)
    assert files
    for f in files:
        assert set(_froms(f)) == {REF}
        text = f.read_text()
        assert text.index("USER root") < text.index("apt-get")
        assert text.index("USER 10001") < text.index("CMD")


def test_env_sets_from(tmp_path, monkeypatch):
    monkeypatch.setenv("STAPEL_BASE_IMAGE", REF)
    proj = _gen(tmp_path, "viaenv")
    files = _py_dockerfiles(proj)
    assert files and all(set(_froms(f)) == {REF} for f in files)


def test_flag_beats_env(monkeypatch):
    monkeypatch.setenv("STAPEL_BASE_IMAGE", "env.example/x:1")
    assert resolve_base_image(REF) == REF
    assert resolve_base_image(None) == "env.example/x:1"
    monkeypatch.delenv("STAPEL_BASE_IMAGE")
    assert resolve_base_image(None) is None


def test_whitespace_ref_refused():
    with pytest.raises(ValueError):
        resolve_base_image("a b")


def test_multistage_cdn_template_keeps_stage_names(tmp_path):
    df = tmp_path / "svc" / "Dockerfile"
    df.parent.mkdir()
    df.write_text(
        "FROM python:3.12-slim-trixie AS vips-builder\nRUN apt-get update\n\n"
        "FROM python:3.12-slim-trixie\nRUN apt-get update\n"
        "ENV DJANGO_SETTINGS_MODULE=x\nCMD [\"gunicorn\"]\n"
    )
    assert apply_base_image(tmp_path, REF) == [df]
    text = df.read_text()
    assert f"FROM {REF} AS vips-builder" in text
    assert text.count("USER root") == 2 and text.count("USER 10001") == 1


def test_image_lint_passes_on_generated_tree(tmp_path):
    proj = _gen(tmp_path, "lint", modules=["core", "cdn"], base_image=REF)
    errors = [v for v in image_lint.lint_project(proj) if v.level == "error"]
    assert errors == []


# --- the runtime user can write what the service writes (T21) -------------

STAPEL_REF = "registry.gitlab.com/stapel-studio/stapel-images/python-base:20260913-5052b912"
_CHOWN_RE = re.compile(r"--chown=(?P<uid>[^:\s]+)")


def _final_stage_owners(text):
    """Simulate who owns each path the FINAL stage creates, and the user the
    container runs as. Conservative where Docker is: WORKDIR and COPY without
    ``--chown`` create root-owned paths, and ``COPY src .`` re-owns WORKDIR."""
    lines = text.splitlines()
    start = max(i for i, ln in enumerate(lines) if ln.upper().startswith("FROM "))
    owners, workdir, user = {}, "/", "0"
    for ln in lines[start + 1:]:
        word, _, rest = ln.partition(" ")
        word = word.upper()
        if word == "USER":
            user = rest.strip()
        elif word == "WORKDIR":
            workdir = rest.strip().rstrip("/") or "/"
            owners.setdefault(workdir, "0")
        elif word == "COPY":
            args = rest.split()
            m = _CHOWN_RE.search(rest)
            dest = args[-1]
            dest = workdir if dest in (".", "./") else (
                dest if dest.startswith("/") else f"{workdir}/{dest.rstrip('/')}")
            owners[dest.rstrip("/") or "/"] = m.group("uid") if m else "0"
        elif word == "RUN":
            for cmd in rest.split("&&"):
                parts = cmd.split()
                if parts[:2] == ["install", "-d"] and "-o" in parts:
                    uid = parts[parts.index("-o") + 1]
                    for p in parts[2:]:
                        if p.startswith("/"):
                            owners[p.rstrip("/")] = uid
                elif parts[:1] == ["chown"]:
                    uid = parts[1].split(":")[0]
                    for p in parts[2:]:
                        owners[p.rstrip("/")] = uid
    return owners, user, workdir


def _owner_of(owners, path):
    p = path.rstrip("/")
    while p:
        if p in owners:
            return owners[p]
        p = p.rpartition("/")[0]
    return owners.get("/", "0")


def _written_paths(proj, svc_dir, workdir):
    """STATIC_ROOT / MEDIA_ROOT as the container sees them: the settings
    defaults (BASE_DIR is the WORKDIR the service is copied into) and every
    compose override."""
    paths = []
    for settings in svc_dir.rglob("settings/base.py"):
        for m in re.finditer(r'(?:STATIC|MEDIA)_ROOT = os\.getenv\("\w+", str\(BASE_DIR((?: / "[^"]+")+)\)\)',
                             settings.read_text()):
            paths.append(workdir + "".join("/" + part for part in re.findall(r'"([^"]+)"', m.group(1))))
    for compose in proj.glob("*.yml"):
        paths += re.findall(r'^\s+(?:STATIC|MEDIA)_ROOT:\s*"?(/[^"\s]+)', compose.read_text(), re.M)
    return paths


@pytest.mark.parametrize("modules", [["core"], ["core", "cdn"]])
def test_runtime_user_owns_every_path_the_service_writes(tmp_path, monkeypatch, modules):
    monkeypatch.delenv("STAPEL_BASE_IMAGE", raising=False)
    proj = _gen(tmp_path, "own", modules=modules, base_image=STAPEL_REF)
    files = _py_dockerfiles(proj)
    assert files
    for f in files:
        owners, user, workdir = _final_stage_owners(f.read_text())
        assert user == "10001", f
        written = _written_paths(proj, f.parent, workdir)
        assert any("staticfiles" in p for p in written) and any("media" in p for p in written), written
        for path in written:
            assert _owner_of(owners, path) == user, (f, path, owners)


def test_final_stage_copies_land_owned_by_the_runtime_user(tmp_path):
    df = tmp_path / "svc" / "Dockerfile"
    df.parent.mkdir()
    df.write_text(
        "FROM python:3.12-slim\nWORKDIR /srv\nCOPY svc/requirements.txt .\n"
        "COPY --chown=1:1 x /x\nCOPY svc .\nENV DJANGO_SETTINGS_MODULE=x\nCMD [\"gunicorn\"]\n"
    )
    apply_base_image(tmp_path, REF)
    text = df.read_text()
    assert "COPY --chown=10001:10001 svc/requirements.txt ." in text
    assert "COPY --chown=1:1 x /x" in text
    assert "install -d -o 10001 -g 10001 /srv/var /srv/staticfiles /srv/media" in text
    assert text.index("install -d") < text.index("USER 10001") < text.index("ENV DJANGO")


# --- the base tier follows the service's need (T20) ------------------------


def test_stapel_base_moves_to_the_tier_the_service_needs(tmp_path, monkeypatch):
    monkeypatch.delenv("STAPEL_BASE_IMAGE", raising=False)
    media = STAPEL_REF.replace("/python-base:", "/media-base:")
    plain = _gen(tmp_path, "plain", modules=["core"], base_image=STAPEL_REF)
    cdn = _gen(tmp_path, "cdn", modules=["core", "cdn"], base_image=STAPEL_REF)
    for f in _py_dockerfiles(plain):
        assert set(_froms(f)) == {STAPEL_REF}
    cdn_files = _py_dockerfiles(cdn)
    assert cdn_files
    for f in cdn_files:
        assert set(_froms(f)) == {media}
    for proj in (plain, cdn):
        findings = [v for v in image_lint.lint_project(proj) if v.rule == "IMG002"]
        assert findings == []


def test_tier_is_never_lowered_and_digests_are_kept(tmp_path):
    from stapel_tools.create_project import tiered_base_image

    df = tmp_path / "Dockerfile"
    (tmp_path / "requirements.txt").write_text("stapel-cdn[images]>=0.10\n")
    text = "FROM python:3.12-slim\nCOPY requirements.txt .\n"
    df.write_text(text)
    ml = STAPEL_REF.replace("/python-base:", "/ml-base:")
    assert tiered_base_image(ml, df, text) == ml
    digest = "registry.gitlab.com/stapel-studio/stapel-images/python-base@sha256:" + "a" * 64
    assert tiered_base_image(digest, df, text) == digest
    assert tiered_base_image(REF, df, text) == REF
    assert tiered_base_image(STAPEL_REF, df, text) == STAPEL_REF.replace("/python-base:", "/media-base:")
