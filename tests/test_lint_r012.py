"""R012 — a view intercepting a refusal the fleet's exception handler owns.

stapel-core 0.61.0 routes twelve DRF refusal types through one seam,
``REST_FRAMEWORK["EXCEPTION_HANDLER"]``. A ``handle_exception`` override that
branches on one of those types takes it back: that endpoint answers a
different body from the rest of the fleet, and recomputes numbers DRF has
already computed. stapel-cdn 0.20.0 was exactly that — ``Throttled`` converted
in the view, answering ``wait + 1``, one second past the ``Retry-After`` on
the same response.

The case the rule must never touch is the module's OWN exception type
(stapel-workspaces' ``BillingSeamMixin`` → ``entitlements.BillingUnavailable``
→ 503): nothing else in the process knows that type, so nothing else can
answer it.
"""
from stapel_tools.lint import scan_file

#: The defect, verbatim in shape: stapel-cdn's describe view before 0.20.0.
CDN_SHAPE = (
    "from rest_framework.exceptions import Throttled\n"
    "from rest_framework.views import APIView\n"
    "\n"
    "\n"
    "class DescribeMediaView(APIView):\n"
    "    def handle_exception(self, exc):\n"
    "        if isinstance(exc, Throttled):\n"
    "            params = {}\n"
    "            if exc.wait is not None:\n"
    "                params['retry_after'] = int(exc.wait) + 1\n"
    "            return StapelErrorResponse(429, ERR_429, params)\n"
    "        return super().handle_exception(exc)\n"
)

#: The legitimate case: stapel-workspaces' BillingSeamMixin.
WORKSPACES_SHAPE = (
    "from stapel_billing import entitlements\n"
    "\n"
    "\n"
    "class BillingSeamMixin:\n"
    "    def handle_exception(self, exc):\n"
    "        if isinstance(exc, entitlements.BillingUnavailable):\n"
    "            return StapelErrorResponse(503, ERR_503_BILLING_UNAVAILABLE)\n"
    "        return super().handle_exception(exc)\n"
)


def _scan(tmp_path, source, name="views.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return [v for v in scan_file(str(path)) if v.rule == "R012"]


# --- the defect ------------------------------------------------------------


def test_the_cdn_shape_is_caught(tmp_path):
    v = _scan(tmp_path, CDN_SHAPE)
    assert len(v) == 1
    assert v[0].line == 7
    assert v[0].level == "error"
    assert "Throttled" in v[0].message
    assert "EXCEPTION_HANDLER" in v[0].message


def test_module_alias_import(tmp_path):
    v = _scan(tmp_path, (
        "from rest_framework import exceptions\n"
        "\n"
        "\n"
        "class V:\n"
        "    def handle_exception(self, exc):\n"
        "        if isinstance(exc, exceptions.NotAuthenticated):\n"
        "            return build()\n"
        "        return super().handle_exception(exc)\n"
    ))
    assert len(v) == 1
    assert "NotAuthenticated" in v[0].message


def test_fully_qualified_reference(tmp_path):
    v = _scan(tmp_path, (
        "import rest_framework\n"
        "\n"
        "\n"
        "class V:\n"
        "    def handle_exception(self, exc):\n"
        "        if isinstance(exc, rest_framework.exceptions.PermissionDenied):\n"
        "            return build()\n"
        "        return super().handle_exception(exc)\n"
    ))
    assert len(v) == 1
    assert "PermissionDenied" in v[0].message


def test_serializers_validation_error_is_the_same_class(tmp_path):
    """``rest_framework.serializers.ValidationError`` IS
    ``rest_framework.exceptions.ValidationError`` — the name most view code
    reaches it by."""
    v = _scan(tmp_path, (
        "from rest_framework import serializers\n"
        "\n"
        "\n"
        "class V:\n"
        "    def handle_exception(self, exc):\n"
        "        if isinstance(exc, serializers.ValidationError):\n"
        "            return build()\n"
        "        return super().handle_exception(exc)\n"
    ))
    assert len(v) == 1


def test_a_try_except_inside_the_override(tmp_path):
    v = _scan(tmp_path, (
        "from rest_framework.exceptions import NotFound\n"
        "\n"
        "\n"
        "class V:\n"
        "    def handle_exception(self, exc):\n"
        "        try:\n"
        "            return super().handle_exception(exc)\n"
        "        except NotFound:\n"
        "            return build()\n"
    ))
    assert len(v) == 1
    assert v[0].line == 8


def test_a_tuple_of_types_reports_each(tmp_path):
    v = _scan(tmp_path, (
        "from rest_framework.exceptions import NotAuthenticated, PermissionDenied\n"
        "\n"
        "\n"
        "class V:\n"
        "    def handle_exception(self, exc):\n"
        "        if isinstance(exc, (NotAuthenticated, PermissionDenied)):\n"
        "            return build()\n"
        "        return super().handle_exception(exc)\n"
    ))
    assert len(v) == 2


def test_a_mixin_outside_views_py_is_scanned(tmp_path):
    """The override moves to a mixin module the first time two views need it;
    a rule routed to views.py only would go blind exactly then."""
    v = _scan(tmp_path, CDN_SHAPE, name="mixins.py")
    assert len(v) == 1


def test_type_comparison_rather_than_isinstance(tmp_path):
    v = _scan(tmp_path, (
        "from rest_framework.exceptions import Throttled\n"
        "\n"
        "\n"
        "class V:\n"
        "    def handle_exception(self, exc):\n"
        "        if type(exc) is Throttled:\n"
        "            return build()\n"
        "        return super().handle_exception(exc)\n"
    ))
    assert len(v) == 1


# --- what must stay quiet --------------------------------------------------


def test_the_workspaces_shape_is_not_a_finding(tmp_path):
    """The module's OWN exception type — the legitimate case that has to
    survive, or the rule gets silenced wholesale."""
    assert _scan(tmp_path, WORKSPACES_SHAPE) == []


def test_a_plain_delegating_override_is_not_a_finding(tmp_path):
    assert _scan(tmp_path, (
        "from rest_framework.exceptions import Throttled\n"
        "\n"
        "\n"
        "class V:\n"
        "    def handle_exception(self, exc):\n"
        "        log(exc)\n"
        "        return super().handle_exception(exc)\n"
    )) == []


def test_raising_a_drf_exception_elsewhere_is_not_a_finding(tmp_path):
    """Raising one leaves the refusal with the fleet handler; the finding is
    deciding on its TYPE inside the override."""
    assert _scan(tmp_path, (
        "from rest_framework.exceptions import Throttled\n"
        "\n"
        "\n"
        "class V:\n"
        "    def post(self, request):\n"
        "        raise Throttled(wait=5)\n"
        "\n"
        "    def handle_exception(self, exc):\n"
        "        return super().handle_exception(exc)\n"
    )) == []


def test_branching_on_a_drf_type_outside_handle_exception_is_not_a_finding(tmp_path):
    assert _scan(tmp_path, (
        "from rest_framework.exceptions import Throttled\n"
        "\n"
        "\n"
        "class V:\n"
        "    def dispatch(self, request, *a, **kw):\n"
        "        try:\n"
        "            return super().dispatch(request, *a, **kw)\n"
        "        except Throttled:\n"
        "            return build()\n"
    )) == []


# --- the escapes -----------------------------------------------------------


def test_noqa_on_the_reported_line(tmp_path):
    assert _scan(tmp_path, CDN_SHAPE.replace(
        "        if isinstance(exc, Throttled):",
        "        if isinstance(exc, Throttled):  # noqa: R012 - see ADR-9",
    )) == []


def test_blanket_noqa(tmp_path):
    assert _scan(tmp_path, CDN_SHAPE.replace(
        "        if isinstance(exc, Throttled):",
        "        if isinstance(exc, Throttled):  # noqa",
    )) == []


def test_the_owns_refusal_marker_covers_the_method(tmp_path):
    assert _scan(tmp_path, CDN_SHAPE.replace(
        "    def handle_exception(self, exc):",
        "    def handle_exception(self, exc):\n"
        "        # stapel: owns-refusal - this endpoint predates the envelope",
    )) == []


def test_the_marker_does_not_cover_a_neighbouring_method(tmp_path):
    """Method-scoped, not file-scoped: the marker in one class's override
    must not silence another's."""
    v = _scan(tmp_path, (
        "from rest_framework.exceptions import Throttled, NotFound\n"
        "\n"
        "\n"
        "class A:\n"
        "    def handle_exception(self, exc):\n"
        "        # stapel: owns-refusal - stated for this one\n"
        "        if isinstance(exc, Throttled):\n"
        "            return build()\n"
        "        return super().handle_exception(exc)\n"
        "\n"
        "\n"
        "class B:\n"
        "    def handle_exception(self, exc):\n"
        "        if isinstance(exc, NotFound):\n"
        "            return build()\n"
        "        return super().handle_exception(exc)\n"
    ))
    assert len(v) == 1
    assert "NotFound" in v[0].message


def test_the_rule_is_registered_in_the_escape_registry(tmp_path):
    """A marker naming a rule the registry does not know suppresses nothing
    and ESC001 says so — R012 has to be in the table."""
    from stapel_tools.escape import RULE_REGISTRY

    assert RULE_REGISTRY["R012"].linter == "stapel-lint"
    assert RULE_REGISTRY["R012"].honors_noqa is True
