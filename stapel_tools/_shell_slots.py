"""Re-export of ``stapel_tools.composition.slots`` (the slot planner moved
there with the platform-neutral composition model)."""
from .composition.slots import (  # noqa: F401
    NONE,
    PAGE_SLOTS,
    SHELL_TEMPLATES_BY_PLATFORM,
    PageEmbedPlan,
    SlotOffer,
    SlotPlan,
    _check_template,
    offer_slots,
    parse_slot_args,
    plan_page_embeds,
    plan_slots,
)
