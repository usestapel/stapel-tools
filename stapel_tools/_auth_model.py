"""Auth model as a scaffold parameter: ``--auth-model`` / ``--auth-kinds`` /
``--posture``.

One composition answers three questions a generated project must not leave
to hand edits:

* **Who gets in** (``model``): ``none`` (no accounts, no sign-in route),
  ``anonymous`` (guest sessions only, no sign-in screen), ``public`` (street
  self-registration, stapel-core's ``public_space`` posture) or ``internal``
  (an in-house tool: registration closed, accounts are created inside —
  ``private_space(door="invite_only")``, password sign-in on).
* **How they sign in** (``kinds``): the ``AUTH_<KIND>_LOGIN`` axes of
  stapel-auth. Listing kinds states every login axis explicitly (the ones not
  listed are off), so the SPA's ``AuthPanel`` — which reads
  ``capabilities()`` — renders exactly the chosen methods. Registration doors
  stay the posture's: the kinds never reopen a door the preset keeps shut
  (password registration in both postures), so no security-relevant override
  and no waiver is ever rendered.
* **Is it launched** (``stage``): the posture's ``stage``. ``live`` (default)
  in the inherited base settings; the local settings module (``dev.py``)
  always declares ``prototype``, so stapel-auth (>= 0.38) resolves mock OTP
  there by itself and logs the code. No ``USE_MOCK_*`` line is ever written.
"""
from __future__ import annotations

from dataclasses import dataclass

AUTH_MODELS = ("none", "anonymous", "public", "internal")
POSTURE_STAGES = ("prototype", "live")

#: kind -> stapel-auth login axis. Order is the rendering order.
AUTH_KINDS: dict[str, str] = {
    "phone": "AUTH_PHONE_LOGIN",
    "email": "AUTH_EMAIL_LOGIN",
    "password": "AUTH_PASSWORD_LOGIN",
    "oauth": "AUTH_OAUTH_LOGIN",
    "magic": "AUTH_MAGIC_LINK_LOGIN",
    "sso": "AUTH_SSO_LOGIN",
    "passkey": "AUTH_PASSKEY_LOGIN",
    "qr": "AUTH_QR_LOGIN",
}

#: Kinds an explicitly chosen model gets when ``--auth-kinds`` is omitted.
#: ``public`` without kinds keeps the library's login defaults (nothing
#: rendered); ``internal`` needs password on, which is off by default.
DEFAULT_KINDS: dict[str, tuple[str, ...]] = {
    "internal": ("password", "email"),
}

#: Library versions the rendered posture needs: the ``stage`` option
#: (stapel-core 0.64) and the stage-derived mock default (stapel-auth 0.38).
#: The registry pins must not fall below them (tests/test_auth_model.py).
POSTURE_FLOORS = {"core": (0, 64, 0), "auth": (0, 38, 0)}

_MOCK_KEYS = ("USE_MOCK_SMS_OTP", "USE_MOCK_EMAIL_OTP")


@dataclass(frozen=True)
class AuthComposition:
    model: str
    kinds: tuple[str, ...] | None = None
    stage: str = "live"

    @property
    def has_login_route(self) -> bool:
        return self.model in ("public", "internal")

    @property
    def needs_auth_module(self) -> bool:
        return self.model != "none"

    @property
    def has_posture(self) -> bool:
        return self.model in ("public", "internal")

    def preset_call(self, stage_expr: str) -> str:
        if self.model == "internal":
            return f'private_space(door="invite_only", stage={stage_expr})'
        return f"public_space(stage={stage_expr})"

    @property
    def preset_name(self) -> str:
        return "private_space" if self.model == "internal" else "public_space"

    def auth_axes(self) -> dict[str, bool]:
        """Non-posture ``STAPEL_AUTH`` keys this composition decides."""
        if self.model == "anonymous":
            axes = {axis: False for axis in AUTH_KINDS.values()}
            axes["AUTH_ANONYMOUS"] = True
            return axes
        if not self.has_posture:
            return {}
        axes: dict[str, bool] = {}
        if self.kinds is not None:
            axes = {axis: kind in self.kinds for kind, axis in AUTH_KINDS.items()}
        if self.model == "internal":
            axes["AUTH_ANONYMOUS"] = False
        return axes


def resolve(
    model: str | None,
    kinds: list[str] | tuple[str, ...] | str | None = None,
    stage: str | None = None,
    *,
    auth_selected: bool,
) -> AuthComposition | None:
    """Validate the CLI values into a composition.

    ``None`` when nothing was asked and auth is not selected (no posture to
    declare). With auth selected and no model given, the composition is
    ``public`` with the library's login defaults — the posture replaces the
    old per-service mock block and changes nothing else.
    """
    if isinstance(kinds, str):
        kinds = [k for k in (p.strip() for p in kinds.split(",")) if k]
    if model is None:
        if kinds:
            raise ValueError("--auth-kinds needs --auth-model public or internal")
        if not auth_selected:
            if stage not in (None, "live"):
                raise ValueError("--posture needs the auth module or --auth-model")
            return None
        model = "public"
    if model not in AUTH_MODELS:
        raise ValueError(
            f"--auth-model {model!r}: choose one of {', '.join(AUTH_MODELS)}"
        )
    stage = stage or "live"
    if stage not in POSTURE_STAGES:
        raise ValueError(
            f"--posture {stage!r}: choose one of {', '.join(POSTURE_STAGES)}"
        )
    if kinds:
        if model not in ("public", "internal"):
            raise ValueError(
                f"--auth-kinds applies to --auth-model public|internal, not {model!r}"
            )
        unknown = [k for k in kinds if k not in AUTH_KINDS]
        if unknown:
            raise ValueError(
                f"--auth-kinds: unknown {', '.join(unknown)} "
                f"(known: {', '.join(AUTH_KINDS)})"
            )
        resolved_kinds: tuple[str, ...] | None = tuple(dict.fromkeys(kinds))
    else:
        resolved_kinds = DEFAULT_KINDS.get(model)
    return AuthComposition(model=model, kinds=resolved_kinds, stage=stage)


def merge_auth_axes(
    module_config: dict[str, dict] | None, comp: AuthComposition | None,
) -> dict[str, dict] | None:
    """Fold the composition's login axes into ``module_config["auth"]``.
    An explicit ``--module-config`` value for the same key wins."""
    if comp is None:
        return module_config
    axes = comp.auth_axes()
    if not axes:
        return module_config
    merged = {**(module_config or {})}
    merged["auth"] = {**axes, **(merged.get("auth") or {})}
    return merged


def render_base_posture(comp: AuthComposition | None, *, stage_expr: str | None = None) -> str:
    """The posture spread for the inherited settings (``base.py``, or the
    minimal preset's ``settings.py``). Explicit ``STAPEL_<MOD>`` values
    rendered above win over the preset; stapel-core's coherence check reports
    any security-relevant difference."""
    if comp is None or not comp.has_posture:
        return ""
    expr = stage_expr or f'"{comp.stage}"'
    return f"""

# ─── Deployment posture (--auth-model {comp.model}) ─────────────────────────────
# Who may get an account here and whether the deployment is launched. The
# preset's values are spread UNDER any explicit STAPEL_AUTH / STAPEL_WORKSPACES
# key above; stapel_core.presets.E001/W001 report every difference at boot.
# stage="prototype" makes stapel-auth mock OTP (the code is logged, never
# sent); flip it to "live" before the deployment is advertised.
from stapel_core.django.presets import {comp.preset_name}  # noqa: E402

_POSTURE = {comp.preset_call(expr)}
STAPEL_POSTURE = _POSTURE["STAPEL_POSTURE"]
STAPEL_AUTH = {{**_POSTURE["STAPEL_AUTH"], **globals().get("STAPEL_AUTH", {{}})}}
STAPEL_WORKSPACES = {{**_POSTURE["STAPEL_WORKSPACES"], **globals().get("STAPEL_WORKSPACES", {{}})}}
"""


def render_local_posture(comp: AuthComposition | None) -> str:
    """The local settings module's own posture: always ``prototype``.

    Empty when the base already declares ``prototype`` (nothing to change)
    or when there is no posture at all. The live floor's two mock pins are
    dropped so the prototype default applies; nothing sets them on."""
    if comp is None or not comp.has_posture or comp.stage == "prototype":
        return ""
    mock_keys = ", ".join(f'"{k}"' for k in _MOCK_KEYS)
    return f"""

# ─── Local posture: prototype stage ─────────────────────────────────────────
# This machine is never a launched deployment. Declaring the prototype stage
# makes stapel-auth mock OTP by itself: the code is logged by this service,
# registration and sign-in complete without an SMS or mail provider. The
# base posture's live floor pins the mock keys off; they are dropped here so
# the stage decides. No USE_MOCK_* value is written anywhere.
from stapel_core.django.presets import {comp.preset_name}  # noqa: E402

_POSTURE = {comp.preset_call('"prototype"')}
STAPEL_POSTURE = _POSTURE["STAPEL_POSTURE"]
STAPEL_AUTH = {{k: v for k, v in STAPEL_AUTH.items() if k not in ({mock_keys})}}
"""
