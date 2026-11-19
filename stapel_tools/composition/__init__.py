"""Platform-neutral scaffold composition (D11).

``model`` holds the frozen dataclasses (the :class:`Composition` every
emitter plans from, and the neutral brick registry types); ``emitter`` the
seam (``build_composition`` → ``Emitter.plan`` → ``write_plan``); one
subpackage per platform holds that platform's binding and emitter.
"""
