"""The static slot assembly (composition spec §4): which installed embed
fills which slot of the chosen shell template. Pure — no I/O, no printing —
so the scaffold, Studio's intake and the tests all ask the same function.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ._shell_templates import (
    MONOLITH_RENDERABLE,
    QUEUE_ITEMS,
    SHELL_TEMPLATES,
)

NONE = "none"

# Page slots: the page a scoped embed names renders them, never a chrome
# (`@stapel/core` `SHELL_SLOT_IDS`, `NavEmbed.scope`). The shell-templates
# mirror lists `page.actions` under sidenav-only; that does not make it chrome.
PAGE_SLOTS: frozenset[str] = frozenset({"page.actions"})


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


@dataclass
class SlotOffer:
    """What the installed pairs offer a template, before any choice.

    ``slots`` maps every slot the template renders to the embeds offered for
    it, sorted ``(order, id)``; ``exclusive`` names the capacity-one slots;
    ``conflicts`` the exclusive slots more than one embed claims — each needs
    a ``--slot <slot>=<id|none>`` before :func:`plan_slots` accepts it.
    """

    template: str
    slots: dict[str, list[dict]]
    exclusive: list[str]
    notes: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    @property
    def conflicts(self) -> dict[str, list[str]]:
        return {
            slot: [e["id"] for e in self.slots[slot]]
            for slot in self.exclusive
            if len(self.slots.get(slot, ())) > 1
        }

    @property
    def embeds(self) -> list[dict]:
        """Every offered embed, in template slot order."""
        return [e for embeds in self.slots.values() for e in embeds]


def offer_slots(
    template: str,
    pairs: list[dict],
    *,
    auth_gated: bool,
    renderable=MONOLITH_RENDERABLE,
) -> SlotOffer:
    """The slots *template* renders and what *pairs*' ``embeds`` offer each —
    the questions an intake asks (which embed on a contested exclusive slot,
    which offered embed to silence) come from here, not from a hand list.
    Raises ``ValueError`` for an unknown or unrenderable template."""
    spec = _check_template(template, renderable)
    rendered: list[str] = [s for s in spec["slots"] if s not in PAGE_SLOTS]
    notes: list[str] = []
    dropped: list[str] = []
    offered: dict[str, list[dict]] = {slot: [] for slot in rendered}
    for pair in pairs:
        for embed in pair.get("embeds") or ():
            if embed["slot"] in PAGE_SLOTS:
                continue  # plan_page_embeds
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
    for claims in offered.values():
        claims.sort(key=lambda e: (e["order"], e["id"]))
    return SlotOffer(
        template=template, slots=offered, exclusive=list(spec["exclusive"]),
        notes=notes, dropped=dropped,
    )


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
    *overrides* are the ``--slot <slotId>=<embedId[,embedId...]|none>``
    choices: one id, a comma list (that subset, in declared order), or
    ``none``.
    Raises ``ValueError`` with the hint the caller prints as is.
    """
    overrides = dict(overrides or {})
    offer = offer_slots(template, pairs, auth_gated=auth_gated, renderable=renderable)
    rendered: list[str] = list(offer.slots)
    exclusive = set(offer.exclusive)
    notes = list(offer.notes)
    dropped = list(offer.dropped)
    offered: dict[str, list[dict]] = {
        slot: list(embeds) for slot, embeds in offer.slots.items()
    }
    for slot, choice in overrides.items():
        if slot not in offered:
            raise ValueError(
                f"--slot {slot}={choice}: {template} has no slot {slot!r}; its "
                "slots are " + ", ".join(rendered)
            )
        if choice == NONE:
            offered[slot] = []
            continue
        # A comma list keeps that subset (hiding one embed of a shared slot);
        # the slot's declared order still decides where each one sits.
        wanted = [part.strip() for part in choice.split(",") if part.strip()]
        known = {e["id"] for e in offered[slot]}
        for embed_id in wanted:
            if embed_id not in known:
                ids = ", ".join(e["id"] for e in offered[slot]) or "none are offered"
                raise ValueError(
                    f"--slot {slot}={choice}: {embed_id!r} is not offered for {slot}; "
                    f"offered: {ids} (or 'none' to silence the slot)"
                )
        offered[slot] = [e for e in offered[slot] if e["id"] in wanted]

    for slot in rendered:
        claims = offered[slot]
        if slot in exclusive and len(claims) > 1:
            ids = sorted(e["id"] for e in claims)
            raise ValueError(
                f"two embeds claim {slot}: {', '.join(ids)} — pick one with "
                f"--slot {slot}=<id> or silence it with --slot {slot}=none"
            )

    return SlotPlan(
        template=template, slots=offered, overrides=overrides,
        notes=notes, dropped=dropped,
    )


@dataclass
class PageEmbedPlan:
    """Scoped page-slot embeds resolved onto the pages this container mounts.

    ``pages`` maps a nav entry id to ``{slot: [embed, ...]}`` in render order
    (``order``, then ``id``); each embed carries ``_package``. ``notes`` and
    ``dropped`` name every page embed left out and why.
    """

    pages: dict[str, dict[str, list[dict]]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def plan_page_embeds(
    pairs: list[dict],
    *,
    auth_gated: bool,
    pages: Mapping[str, Iterable[str]],
) -> PageEmbedPlan:
    """Place every page-slot embed (``page.actions``) on the page its
    ``scope.navEntry`` names. *pages* maps each nav entry this container
    mounts to the page slots its page renders (``NAV_ENTRY_MOUNTS``
    ``page_slots``). An embed is dropped with a note when it has no scope,
    when its page is not mounted, when that page renders no such slot, or
    when it is member-only and nothing is gated."""
    plan = PageEmbedPlan()
    slots_of = {entry: set(slots) for entry, slots in pages.items()}
    for pair in pairs:
        for embed in pair.get("embeds") or ():
            slot = embed["slot"]
            if slot not in PAGE_SLOTS:
                continue
            target = (embed.get("scope") or {}).get("navEntry")
            reason = None
            if not target:
                reason = f"is a {slot} embed without a scope, so no page renders it"
            elif target not in slots_of:
                reason = f"is scoped to {target}, which this project does not mount"
            elif slot not in slots_of[target]:
                reason = f"is scoped to {target}, whose page renders no {slot} slot"
            elif embed.get("surface", "any") == "member" and not auth_gated:
                reason = "is member-only, and this composition has no gated shell"
            if reason is not None:
                plan.dropped.append(embed["id"])
                plan.notes.append(f"embed {embed['id']} {reason}")
                continue
            plan.pages.setdefault(target, {}).setdefault(slot, []).append(
                {**embed, "_package": pair["package"]}
            )
    for slots in plan.pages.values():
        for claims in slots.values():
            claims.sort(key=lambda e: (e["order"], e["id"]))
    return plan


def parse_slot_args(values: list[str] | None) -> dict[str, str]:
    """``["topbar.end=none", ...]`` -> ``{"topbar.end": "none"}``."""
    out: dict[str, str] = {}
    for raw in values or ():
        slot, sep, choice = raw.partition("=")
        if not sep or not slot.strip() or not choice.strip():
            raise ValueError(
                f"--slot {raw!r}: expected SLOT=EMBED[,EMBED...] or SLOT=none"
            )
        out[slot.strip()] = choice.strip()
    return out
