"""R013 — a list endpoint answering a hand-built, unbounded page envelope.

The defect, in the shape a generated product shipped it: a ``get`` that builds
``{"results": rows, "count": n}`` by hand over an unsliced queryset. It looks
paginated on the wire, returns the whole table, and gives the client no anchor.
"""
from stapel_tools.escape import RULE_REGISTRY
from stapel_tools.lint import scan_file

UNBOUNDED = (
    "class PartListView(StapelAPIView):\n"
    "    def get(self, request):\n"
    "        parts, count = services.filter_parts(q=request.query_params.get('q'))\n"
    "        return self.serialized_response({'results': parts, 'count': count})\n"
)


def _scan(tmp_path, source, name="views.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return [v for v in scan_file(str(path)) if v.rule == "R013"]


def test_hand_built_envelope_without_a_bound_is_reported(tmp_path):
    found = _scan(tmp_path, UNBOUNDED)
    assert [(v.line, v.level) for v in found] == [(4, "error")]
    assert "PartListView.get" in found[0].message
    assert "AnchorPagination" in found[0].message


def test_items_key_and_list_method_count_too(tmp_path):
    source = (
        "class Feed(viewsets.ViewSet):\n"
        "    def list(self, request):\n"
        "        payload = {'items': list(Entry.objects.all())}\n"
        "        return StapelResponse(FeedSerializer(payload).data)\n"
    )
    assert [v.line for v in _scan(tmp_path, source, "viewsets.py")] == [3]


def test_pagination_class_on_the_view_or_a_local_base_exempts(tmp_path):
    source = (
        "class Base(StapelAPIView):\n"
        "    pagination_class = AnchorPagination\n"
        "\n"
        "class PartListView(Base):\n"
        "    def get(self, request):\n"
        "        return self.serialized_response({'results': rows(), 'count': 1})\n"
    )
    assert _scan(tmp_path, source) == []


def test_pagination_class_none_does_not_exempt(tmp_path):
    source = UNBOUNDED.replace(
        "    def get", "    pagination_class = None\n\n    def get")
    assert len(_scan(tmp_path, source)) == 1


def test_a_slice_or_a_limit_in_the_method_is_a_bound(tmp_path):
    sliced = UNBOUNDED.replace("'results': parts", "'results': parts[:50]")
    limited = UNBOUNDED.replace("q=request", "limit=50, q=request")
    assert _scan(tmp_path, sliced) == []
    assert _scan(tmp_path, limited) == []


def test_inline_openapi_schema_with_items_is_not_a_body(tmp_path):
    source = (
        "class AuthView(APIView):\n"
        "    @extend_schema(responses={200: {'type': 'object', 'properties': {\n"
        "        'languages': {'type': 'array', 'items': {'type': 'string'}}}}})\n"
        "    def get(self, request):\n"
        "        schema = {'type': 'array', 'items': {'type': 'string'}}\n"
        "        return StapelResponse({'valid': True, 'schema': schema})\n"
    )
    assert _scan(tmp_path, source) == []


def test_other_handlers_and_non_view_modules_are_out_of_scope(tmp_path):
    post = UNBOUNDED.replace("def get", "def post")
    assert _scan(tmp_path, post) == []
    assert _scan(tmp_path, UNBOUNDED, name="services.py") == []


def test_noqa_on_the_line_or_the_method_silences_and_is_registered(tmp_path):
    on_line = UNBOUNDED.replace("'count': count})", "'count': count})  # noqa: R013")
    on_def = UNBOUNDED.replace("def get(self, request):",
                               "def get(self, request):  # noqa: R013")
    assert _scan(tmp_path, on_line) == []
    assert _scan(tmp_path, on_def) == []
    assert RULE_REGISTRY["R013"].linter == "stapel-lint"


# ── AU3-18: the bound must reach the value in the envelope ───────────


def test_a_bound_elsewhere_in_the_method_does_not_bound_the_envelope(tmp_path):
    """False negative: a slice and a ``limit`` name that never touch the rows."""
    source = (
        "class PartListView(StapelAPIView):\n"
        "    def get(self, request):\n"
        "        limit = int(request.query_params.get('limit', 20))\n"
        "        recent = Part.objects.order_by('-id')[:5]\n"
        "        parts = Part.objects.all()\n"
        "        return self.serialized_response({'results': parts, 'recent': recent})\n"
    )
    assert [v.line for v in _scan(tmp_path, source)] == [6]


def test_a_bound_on_the_assigned_rows_is_followed(tmp_path):
    source = (
        "class PartListView(StapelAPIView):\n"
        "    def get(self, request):\n"
        "        limit = int(request.query_params.get('limit', 20))\n"
        "        qs = Part.objects.all()\n"
        "        parts = list(qs[:limit])\n"
        "        return self.serialized_response({'results': parts})\n"
    )
    assert _scan(tmp_path, source) == []


def test_paginate_queryset_on_the_rows_is_a_bound(tmp_path):
    source = (
        "class PartListView(StapelAPIView):\n"
        "    def get(self, request):\n"
        "        page = self.paginator.paginate_queryset(Part.objects.all(), request)\n"
        "        return self.serialized_response({'results': page})\n"
    )
    assert _scan(tmp_path, source) == []


def test_a_detail_view_with_a_lookup_kwarg_is_out_of_scope(tmp_path):
    """False positive: a cart's lines are the cart, not a page of a table."""
    source = (
        "class CartView(StapelAPIView):\n"
        "    def get(self, request, cart_id):\n"
        "        cart = services.get_cart(cart_id)\n"
        "        return self.serialized_response({'id': cart.id, 'items': cart.lines})\n"
    )
    assert _scan(tmp_path, source) == []


def test_args_and_kwargs_alone_are_not_a_lookup(tmp_path):
    source = UNBOUNDED.replace("def get(self, request):",
                               "def get(self, request, *args, **kwargs):")
    assert len(_scan(tmp_path, source)) == 1
