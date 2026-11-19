"""The KMP binding of the brick registry, read from the vendored stapel-kmp data.

``vendored/kmp-registry.json`` (digest-pinned, see :mod:`.vendor`) names the
train, the Maven group, the BOM and catalog, the app builder, the offline
module and, per brick, its domain / UI / testing artifacts and the
``StapelBrick`` descriptor. A brick whose descriptor stapel-kmp marks present
ships ``nav-manifest.json`` (vendored as ``nav-manifests/<brick>.json``); its
binding lists exactly the ids that file declares.

A brick whose descriptor stapel-kmp does not mark present ships nothing an
app can install by name: the emitter lists it as ``pending`` (never writes a
stand-in descriptor), and the gate names it on every run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from ..bricks import BRICK_UI
from . import vendor


@dataclass(frozen=True)
class KmpBinding:
    """One brick's stapel-kmp delivery: artifacts, descriptor, the ids it ships."""

    key: str
    domain_artifact: str
    ui_artifact: Optional[str]
    testing_artifact: Optional[str]
    version: str
    descriptor_class: str
    descriptor_call: str
    descriptor_present: bool
    entries: tuple[str, ...] = ()
    embeds: tuple[str, ...] = ()
    presentation: dict = field(default_factory=dict)
    requires: dict = field(default_factory=dict)
    released: bool = False

    @property
    def descriptor_package(self) -> str:
        return self.descriptor_class.rsplit(".", 1)[0]

    @property
    def descriptor_name(self) -> str:
        return self.descriptor_class.rsplit(".", 1)[1]


#: Sign-in kinds the mobile client completes today (ruling R14): the others need a
#: native hand-off (OAuth / magic link redirect, WebAuthn origins, SSO) that does not exist yet.
MOBILE_AUTH_KINDS: tuple[str, ...] = ("phone", "email", "password")


@lru_cache(maxsize=1)
def kmp_registry() -> dict:
    return json.loads(vendor.read_bytes("kmp-registry.json"))


@lru_cache(maxsize=1)
def shell_templates() -> dict:
    """template id -> {"slots": [union of every width's slots, first-seen order], "chrome": {...}}."""
    doc = json.loads(vendor.read_bytes("shell-templates.json"))
    out: dict[str, dict] = {}
    for template in doc["templates"]:
        slots: list[str] = []
        for width in ("phone", "tablet", "desktop"):
            for slot in template["slots"].get(width, ()):
                if slot not in slots:
                    slots.append(slot)
        out[template["id"]] = {"slots": slots, "chrome": dict(template.get("chrome", {}))}
    return out


def nav_manifest(key: str) -> Optional[dict]:
    try:
        return json.loads(vendor.read_bytes(f"nav-manifests/{key}.json"))
    except KeyError:
        return None


@lru_cache(maxsize=1)
def kmp_bindings() -> dict[str, KmpBinding]:
    reg = kmp_registry()
    artifacts = reg.get("artifacts", {})
    out: dict[str, KmpBinding] = {}
    for key, brick in reg.get("bricks", {}).items():
        descriptor = brick.get("descriptor") or {}
        present = bool(descriptor.get("present"))
        ui = brick.get("ui")
        manifest = nav_manifest(key) if present else None
        if manifest is not None:
            entries = tuple(d["id"] for d in manifest.get("destinations", ()))
            embeds = tuple(e["id"] for e in manifest.get("embeds", ()))
            presentation = {d["id"]: d.get("presentation", "screen")
                            for d in manifest.get("destinations", ())}
            requires = {d["id"]: tuple(d.get("requires", ()))
                        for d in manifest.get("destinations", ()) if d.get("requires")}
        else:
            entries, embeds, presentation, requires = (), (), {}, {}
        version = artifacts.get(ui or brick["domain"], {}).get("version", reg["train"])
        out[key] = KmpBinding(
            key=key,
            domain_artifact=brick["domain"],
            ui_artifact=ui,
            testing_artifact=brick.get("testing"),
            version=version,
            descriptor_class=descriptor.get("class", ""),
            descriptor_call=descriptor.get("call", ""),
            descriptor_present=present,
            entries=tuple(entries),
            embeds=tuple(embeds),
            presentation=presentation,
            requires=requires,
            released=all(artifacts.get(a, {}).get("released", False)
                         for a in (brick["domain"], ui) if a),
        )
    return out


def unreleased_artifacts(keys: tuple[str, ...], *, offline: bool) -> list[str]:
    """Every artifact a generated app with bricks *keys* resolves that Maven Central lacks."""
    reg = kmp_registry()
    artifacts = reg.get("artifacts", {})
    needed = [reg["catalog"], reg["bom"], reg["app"]["artifact"], "stapel-ui-material3", "stapel-wire",
              reg["plugins"]["codegen"]["artifact"], reg["plugins"]["tokens"]["artifact"]]
    if offline:
        needed.append(reg["app"]["offline"]["artifact"])
    for key in keys:
        b = kmp_bindings()[key]
        needed += [a for a in (b.domain_artifact, b.ui_artifact, b.testing_artifact) if a]
    return sorted({a for a in needed if not artifacts.get(a, {}).get("released", False)})


def project_kmp(key: str) -> list[dict]:
    """The neutral destinations of *key* the KMP binding ships, in nav-manifest shape (shared fields)."""
    binding = kmp_bindings()[key]
    brick = BRICK_UI.get(key)
    out = []
    for entry in brick.nav if brick else ():
        if entry.id not in binding.entries:
            continue
        doc = {
            "id": entry.id, "labelKey": entry.label_key, "icon": entry.icon, "path": entry.path,
            "level": entry.level, "parentId": entry.parent_id,
            "menuVisibleDefault": entry.menu_visible_default, "requiresAuth": entry.requires_auth,
            "surface": entry.surface, "order": entry.order,
        }
        if entry.short_label_key is not None:
            doc["shortLabelKey"] = entry.short_label_key
        out.append(doc)
    return out


#: nav-manifest destination fields that are KMP-only (not in the neutral entry).
KMP_ONLY_FIELDS = ("presentation", "requires")


def check_bindings() -> list[str]:
    """Every id a KMP binding lists is declared by its neutral brick."""
    problems = []
    for key, binding in kmp_bindings().items():
        brick = BRICK_UI.get(key)
        nav_ids = {e.id for e in brick.nav} if brick else set()
        embed_ids = {e.id for e in brick.embeds} if brick else set()
        for entry in binding.entries:
            if entry not in nav_ids:
                problems.append(f"kmp {key}: entry {entry} is not declared by the neutral brick")
        for embed in binding.embeds:
            if embed not in embed_ids:
                problems.append(f"kmp {key}: embed {embed} is not declared by the neutral brick")
    return problems
