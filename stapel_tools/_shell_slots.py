"""The static slot assembly (composition spec §4): which installed embed
fills which slot of the chosen shell template. Pure — no I/O, no printing —
so the scaffold, Studio's intake and the tests all ask the same function.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ._shell_templates import (
    MONOLITH_RENDERABLE,
    QUEUE_ITEMS,
    SHELL_TEMPLATES,
)

NONE = "none"


@dataclass
class SlotPlan:
    """The resolved composition of one shell.

    ``slots`` maps every slot the template renders to its ordered embeds
    (an empty list = nothing fills it). Each embed is the pair's declaration
    plus ``_package``, the npm package it is imported from. ``notes`` names
    every offered embed that was dropped and why.
    """

    template: str
    slots: dict[str, list[dict]]
    overrides: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    @property
    def filled(self) -> dict[str, list[dict]]:
        """Only the slots something fills, in the template's slot order."""
        return {slot: embeds for slot, embeds in self.slots.items() if embeds}


def _check_template(template: str, renderable) -> dict:
    if template not in SHELL_TEMPLATES:
        raise ValueError(
            f"--shell {template!r} is not a shell template; one of "
            + ", ".join(SHELL_TEMPLATES)
        )
    if template not in renderable:
        item = QUEUE_ITEMS.get(template, f"--shell {template}")
        raise ValueError(
            f"--shell {template} is not rendered by the monolith generator yet "
            f"(queued: {item!r}); use --shell "
            + " or ".join(sorted(renderable))
        )
    return SHELL_TEMPLATES[template]


def plan_slots(
    template: str,
    pairs: list[dict],
    *,
    auth_gated: bool,
    overrides: dict[str, str] | None = None,
    renderable=MONOLITH_RENDERABLE,
) -> SlotPlan:
    """Resolve *pairs*' ``embeds`` onto *template*.

    *pairs* are registry entries (``{"package": ..., "embeds": [...]}``);
    *overrides* are the ``--slot <slotId>=<embedId|none>`` choices.
    Raises ``ValueError`` with the hint the caller prints as is.
    """
    overrides = dict(overrides or {})
    spec = _check_template(template, renderable)
    rendered: list[str] = list(spec["slots"])
    exclusive = set(spec["exclusive"])

    notes: list[str] = []
    dropped: list[str] = []
    offered: dict[str, list[dict]] = {slot: [] for slot in rendered}
    for pair in pairs:
        for embed in pair.get("embeds") or ():
            if embed["slot"] not in offered:
                dropped.append(embed["id"])
                notes.append(
                    f"embed {embed['id']} wants {embed['slot']}, which {template} "
                    "has no slot for"
                )
                continue
            if embed.get("surface", "any") == "member" and not auth_gated:
                dropped.append(embed["id"])
                notes.append(
                    f"embed {embed['id']} is member-only, and this composition "
                    "has no gated shell to render it in"
                )
                continue
            offered[embed["slot"]].append({**embed, "_package": pair["package"]})

    for slot, choice in overrides.items():
        if slot not in offered:
            raise ValueError(
                f"--slot {slot}={choice}: {template} has no slot {slot!r}; its "
                "slots are " + ", ".join(rendered)
            )
        if choice == NONE:
            offered[slot] = []
            continue
        chosen = [e for e in offered[slot] if e["id"] == choice]
        if not chosen:
            ids = ", ".join(e["id"] for e in offered[slot]) or "none are offered"
            raise ValueError(
                f"--slot {slot}={choice}: {choice!r} is not offered for {slot}; "
                f"offered: {ids} (or 'none' to silence the slot)"
            )
        offered[slot] = chosen

    for slot in rendered:
        claims = offered[slot]
        if slot in exclusive and len(claims) > 1:
            ids = sorted(e["id"] for e in claims)
            raise ValueError(
                f"two embeds claim {slot}: {', '.join(ids)} — pick one with "
                f"--slot {slot}=<id> or silence it with --slot {slot}=none"
            )
        claims.sort(key=lambda e: (e["order"], e["id"]))

    return SlotPlan(
        template=template, slots=offered, overrides=overrides,
        notes=notes, dropped=dropped,
    )


def parse_slot_args(values: list[str] | None) -> dict[str, str]:
    """``["topbar.end=none", ...]`` -> ``{"topbar.end": "none"}``."""
    out: dict[str, str] = {}
    for raw in values or ():
        slot, sep, choice = raw.partition("=")
        if not sep or not slot.strip() or not choice.strip():
            raise ValueError(f"--slot {raw!r}: expected SLOT=EMBED or SLOT=none")
        out[slot.strip()] = choice.strip()
    return out
