"""The scaffolded nginx never serves stapel-cdn's protected/ and private/ trees.

``location /media/ { alias /media/; }`` served the whole media volume, so the
clean (unwatermarked) renditions at the predictable
``/media/<slug>/protected/<type>/<hash>/clean/720w.webp`` and every
``private/file/<hash>/<name>`` original were public to anyone who saw a ref.
The test resolves request paths against the generated server blocks with
nginx's location-selection rules.
"""
import re

import pytest

from stapel_tools.create_project import create_project


def _create(tmp_path):
    create_project(
        name="app", project_type="monolith", title="App", url="https://x.dev",
        company_name="X", company_email="x@x.dev", modules=["core", "cdn"],
        output_dir=tmp_path, use_submodules=False, init_git=False,
    )
    return tmp_path / "app"


def _locations(conf: str):
    """``[(modifier, pattern, body)]`` for each top-level location block."""
    out = []
    for match in re.finditer(r"location\s+(=|~\*|~|\^~)?\s*(\S+)\s*\{(.*?)\n    \}", conf, re.S):
        out.append((match.group(1) or "", match.group(2), match.group(3)))
    return out


def _select(conf: str, uri: str) -> str:
    """The body of the location nginx picks for *uri*."""
    locations = _locations(conf)
    for mod, pattern, body in locations:
        if mod == "=" and pattern == uri:
            return body
    prefixes = [(p, m, b) for m, p, b in locations if m in ("", "^~") and uri.startswith(p)]
    best = max(prefixes, key=lambda item: len(item[0]), default=None)
    if best is not None and best[1] == "^~":
        return best[2]
    for mod, pattern, body in locations:
        if mod == "~" and re.search(pattern, uri):
            return body
        if mod == "~*" and re.search(pattern, uri, re.I):
            return body
    return best[2] if best else ""


def _confs(proj):
    return {
        "prod": (proj / "service-configs" / "nginx" / "nginx.conf").read_text(),
        "local": (proj / "service-configs" / "nginx-local" / "default.conf.template").read_text(),
    }


@pytest.mark.parametrize("uri", [
    "/media/app/protected/product/abc123/clean/720w.webp",
    "/media/app/protected/product/abc123/original/x.jpg",
    "/media/app/private/file/abc123/contract.pdf",
    "/media/app/private/audio/abc123/voice.webm",
])
def test_protected_and_private_media_are_not_served(tmp_path, uri):
    for name, conf in _confs(_create(tmp_path)).items():
        body = _select(conf, uri)
        assert "return 404" in body, f"{name} serves {uri}"


@pytest.mark.parametrize("uri", [
    "/media/app/product/abc123/720w.webp",
    "/media/app/avatar/abc123/64.webp",
])
def test_public_variants_are_still_served(tmp_path, uri):
    confs = _confs(_create(tmp_path))
    assert "alias /media/" in _select(confs["prod"], uri)
    assert "proxy_pass $stapel_backend" in _select(confs["local"], uri)
