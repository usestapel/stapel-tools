"""A named volume shared by nginx and a backend must not be seeded from nginx.

Docker copies the image's content (and the directory owner) at the mount
point into an EMPTY named volume, from whichever container starts first.
nginx:alpine has a root-owned ``/media``; when nginx won the start race the
backend (uid 10001) found ``/app/media`` owned by root and refused to boot on
``stapel_core.storage.E002`` (MEDIA_ROOT cannot be created). The nginx side
of every shared volume therefore mounts with ``nocopy``.
"""
import pytest
import yaml

from stapel_tools.create_project import create_project


def _gen(tmp_path, project_type, modules):
    create_project(
        name="vol", project_type=project_type, title="vol", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=modules,
        output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    return tmp_path / "vol"


def _named_volume_mounts(volumes):
    """(volume name, target, mount dict or None) for each named volume."""
    out = []
    for vol in volumes or []:
        if isinstance(vol, str):
            source, _, rest = vol.partition(":")
            if source and not source.startswith((".", "/", "~", "$")):
                out.append((source, rest.split(":", 1)[0], None))
        elif vol.get("type") == "volume":
            out.append((vol["source"], vol["target"], vol))
    return out


def _backend_volumes(proj):
    names = set()
    for svc_file in proj.glob("svc-*.yml"):
        for svc in (yaml.safe_load(svc_file.read_text()).get("services") or {}).values():
            names |= {name for name, _target, _m in _named_volume_mounts(svc.get("volumes"))}
    return names


@pytest.mark.parametrize("project_type,modules", [
    ("monolith", ["core"]),
    ("monolith", ["core", "cdn"]),
    ("microservices", ["core"]),
])
def test_nginx_never_seeds_a_volume_the_backend_writes(tmp_path, project_type, modules):
    proj = _gen(tmp_path, project_type, modules)
    # A microservices base has no service yet: every service stapel-new-service
    # adds mounts the same two volumes.
    shared = _backend_volumes(proj) or {"static-content", "media-content"}
    assert "media-content" in shared, shared
    base = yaml.safe_load((proj / "docker-compose.base.yml").read_text())
    nginx = base["services"]["nginx"]
    mounts = {name: (target, m) for name, target, m in _named_volume_mounts(nginx["volumes"])}
    assert "media-content" in mounts, mounts
    for name in shared & set(mounts):
        target, mount = mounts[name]
        assert mount is not None and (mount.get("volume") or {}).get("nocopy") is True, (
            f"nginx mounts {name} at {target} without nocopy")
        assert mount.get("read_only") is True
