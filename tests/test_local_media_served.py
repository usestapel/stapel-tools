"""The local stack serves uploaded media the way prod does: from the volume.

The local nginx proxied ``/media/`` to Django while the dev ``config/urls.py``
mounts no media route, so every photo and avatar answered 404 although the
files were in the media volume. The test follows one public media URL from
``MEDIA_URL`` through the local nginx location to the file the backend wrote
under ``MEDIA_ROOT``, across the compose mounts; ``scripts/smoke-media.sh``
does the same against a booted stack.
"""
import re
import subprocess

import yaml

from stapel_tools.create_project import create_project


def _create(tmp_path, modules=("core", "cdn")):
    create_project(
        name="app", project_type="monolith", title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=list(modules),
        output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    return tmp_path / "app"


def _volume_targets(volumes, name):
    out = []
    for vol in volumes or []:
        if isinstance(vol, str):
            source, _, rest = vol.partition(":")
            if source == name:
                out.append(rest.split(":", 1)[0])
        elif vol.get("type") == "volume" and vol.get("source") == name:
            out.append(vol["target"])
    return out


def _media_location(conf):
    match = re.search(r"\n    location /media/ \{(.*?)\n    \}", conf, re.S)
    assert match, "no `location /media/` in the local nginx"
    return match.group(1)


def test_a_media_url_reaches_the_file_the_backend_wrote(tmp_path):
    proj = _create(tmp_path)
    base = (proj / "svc-app" / "config" / "settings" / "base.py").read_text()
    media_url = re.search(r'^MEDIA_URL = "([^"]+)"', base, re.M).group(1)
    assert media_url == "/media/app/"

    svc = yaml.safe_load((proj / "svc-app.yml").read_text())["services"]["svc-app"]
    media_root = svc["environment"]["MEDIA_ROOT"]
    [backend_mount] = _volume_targets(svc["volumes"], "media-content")
    assert media_root.startswith(backend_mount.rstrip("/") + "/")
    in_volume = media_root[len(backend_mount.rstrip("/")) + 1:]  # "app/"

    body = _media_location(
        (proj / "service-configs" / "nginx-local" / "default.conf.template").read_text()
    )
    assert "proxy_pass" not in body, "dev urls.py has no media route to proxy to"
    alias = re.search(r"alias\s+(\S+);", body).group(1)

    local = yaml.safe_load((proj / "docker-compose.local.yml").read_text())
    [nginx_mount] = _volume_targets(local["services"]["nginx"]["volumes"], "media-content")
    assert alias.rstrip("/") == nginx_mount.rstrip("/")

    # /media/app/x.png -> alias + "app/x.png" -> volume "app/x.png" -> MEDIA_ROOT/x.png
    rel = "photo/abc/720w.webp"
    served = alias + (media_url + rel)[len("/media/"):]
    written = nginx_mount.rstrip("/") + "/" + in_volume + rel
    assert served == written


def test_local_nginx_does_not_seed_the_media_volume(tmp_path):
    proj = _create(tmp_path)
    local = yaml.safe_load((proj / "docker-compose.local.yml").read_text())
    [mount] = [
        v for v in local["services"]["nginx"]["volumes"]
        if isinstance(v, dict) and v.get("source") == "media-content"
    ]
    assert mount["read_only"] is True
    assert mount["volume"]["nocopy"] is True


def test_smoke_media_script_and_target_are_generated(tmp_path):
    proj = _create(tmp_path)
    script = proj / "scripts" / "smoke-media.sh"
    text = script.read_text()
    assert 'SERVICE="${SERVICE:-svc-app}"' in text
    assert "default_storage.save" in text and "image/png" in text
    assert subprocess.run(["sh", "-n", str(script)]).returncode == 0
    makefile = (proj / "Makefile").read_text()
    assert "smoke-local:" in makefile
    assert "sh scripts/smoke-media.sh" in makefile


def test_smoke_media_fails_when_nothing_was_stored(tmp_path):
    """The script's own red path: no backend answer is a failure, not a skip."""
    proj = _create(tmp_path)
    result = subprocess.run(
        ["sh", "scripts/smoke-media.sh", "http://127.0.0.1:9"],
        cwd=proj, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "COMPOSE": "false"},
    )
    assert result.returncode == 1
    assert "did not store a file" in result.stderr
