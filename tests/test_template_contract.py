"""stapel_tools.template_contract — the scanner, the AST reader, the gate.

The mechanism is exercised end-to-end by the module that owns the first
``docs/templates.json`` (stapel-notifications). These are the unit-level
properties that mechanism rests on, plus the loud failures — an emitter that
degrades to a partial artifact is worse than no artifact, because a partial
one still reads like a contract.
"""
import textwrap
from pathlib import Path

import pytest

from stapel_tools.template_contract import (
    EmitError,
    Route,
    build_document,
    declared_for,
    resolve_chain,
    scan_call_site,
    scan_source,
)


# ── the template scanner ─────────────────────────────────────────────────────

def test_reads_variables_through_djangos_own_parser():
    scan = scan_source("{{ a }}{{ b.c.d }}{{ 'literal' }}{{ 42 }}", name="t.html")
    assert scan.variables == {"a": "required", "b": "required"}


def test_a_default_filter_makes_a_variable_optional():
    scan = scan_source('{{ colour|default:"#fff" }}', name="t.html")
    assert scan.variables == {"colour": "optional"}


def test_a_filter_argument_is_a_context_read():
    """``{{ host|default:name }}`` reads BOTH. Missing this is how a scanner
    quietly under-declares."""
    scan = scan_source("{{ host|default:name }}", name="t.html")
    assert set(scan.variables) == {"host", "name"}


def test_an_if_condition_is_an_optional_read():
    scan = scan_source("{% if role %}x{% endif %}", name="t.html")
    assert scan.variables == {"role": "optional"}


def test_one_unguarded_read_makes_a_variable_required():
    scan = scan_source("{% if x %}y{% endif %}{{ x }}", name="t.html")
    assert scan.variables["x"] == "required"


def test_loop_and_with_names_are_locals_not_context():
    scan = scan_source(
        "{% for row in rows %}{{ row.a }}{{ forloop.counter }}{% endfor %}"
        "{% with total=count %}{{ total }}{% endwith %}",
        name="t.html",
    )
    assert set(scan.variables) == {"rows", "count"}


def test_assignment_tag_result_is_a_local():
    scan = scan_source("{% mytag as root %}{{ root }}", name="t.html")
    assert scan.variables == {}
    assert scan.unknown_tags == ("mytag",)


def test_comment_blocks_are_not_scanned():
    scan = scan_source("{% comment %}{{ ghost }}{% endcomment %}{{ real }}", name="t.html")
    assert set(scan.variables) == {"real"}


def test_extends_and_includes_are_recorded_with_their_guard():
    scan = scan_source(
        '{% extends "base.html" %}'
        '{% include "always.html" %}'
        '{% if flag %}{% include "sometimes.html" %}{% endif %}',
        name="t.html",
    )
    assert scan.extends == "base.html"
    assert scan.includes == ("always.html", "sometimes.html")
    assert scan.guarded_includes == ("sometimes.html",)


def test_unmodelled_construct_is_loud_under_strict():
    with pytest.raises(EmitError, match="unmodelled template construct"):
        scan_source("{% weird %}", name="t.html", strict=True)


# ── tag argument grammars ────────────────────────────────────────────────────
# A tag's arguments are its own small grammar, not a list of expressions. The
# scanner once approximated that ("an `=` means a kwarg, anything else is an
# expression"), so every bare option flag was reported as a context variable:
# `{% blocktranslate trimmed %}` demanded a `trimmed` that Django never binds
# and no host can pass. Each option below is checked against Django's own
# parser, so these are the real forms and not forms we found convenient.

#: every option of the tags the scanner models, one source per option
GRAMMAR_CASES = [
    # (source, expected context reads, expected locals — readable, not context)
    ("{% blocktranslate trimmed %}{{ code }}{% endblocktranslate %}", {"code"}, ""),
    ("{% blocktranslate with n=user.name %}{{ n }}{% endblocktranslate %}", {"user"}, ""),
    ("{% blocktranslate with a|upper as b and c as d %}{{ b }}{{ d }}"
     "{% endblocktranslate %}", {"a", "c"}, ""),
    ("{% blocktranslate count n=items|length %}{{ n }}{% plural %}{{ n }}"
     "{% endblocktranslate %}", {"items"}, ""),
    ('{% blocktranslate context "a greeting" %}{{ code }}{% endblocktranslate %}',
     {"code"}, ""),
    ("{% blocktranslate context ctx %}x{% endblocktranslate %}", {"ctx"}, ""),
    ("{% blocktranslate asvar greeting %}hi{% endblocktranslate %}{{ greeting }}",
     set(), "greeting"),
    ('{% translate "hi" noop %}', set(), ""),
    ('{% translate "hi" context "greeting" %}', set(), ""),
    ('{% translate "hi" as greeting %}{{ greeting }}', set(), "greeting"),
    ("{% translate subject %}", {"subject"}, ""),
    ('{% include "x.html" only %}', set(), ""),
    ('{% include "x.html" with a=b only %}', {"b"}, ""),
    ("{% with total=count %}{{ total }}{% endwith %}", {"count"}, ""),
    ("{% with person.method as total %}{{ total }}{% endwith %}", {"person"}, ""),
    ("{% for row in rows reversed %}{{ row }}{% endfor %}", {"rows"}, ""),
    ('{% now "Y" as year %}{{ year }}', set(), "year"),
]


@pytest.mark.parametrize("source,reads,_locals", GRAMMAR_CASES)
def test_an_option_is_read_as_an_option_not_as_a_context_variable(source, reads, _locals):
    """The defect this table exists for: `trimmed`, `asvar`, `context`, `count`,
    `noop`, `only`, `reversed` are grammar, not variables a host can supply."""
    scan = scan_source(source, name="t.html", strict=True)
    assert set(scan.variables) == reads


@pytest.mark.parametrize("source,_reads,local", GRAMMAR_CASES)
def test_an_option_that_binds_a_name_binds_it_as_a_local(source, _reads, local):
    """`asvar`/`as` store the tag's RESULT under a name that is readable after
    the tag — a local, and never something the host is asked to pass in."""
    if not local:
        pytest.skip("this form binds nothing")
    scan = scan_source(source, name="t.html", strict=True)
    assert local not in scan.variables


@pytest.mark.parametrize("source,_reads,_locals", GRAMMAR_CASES)
def test_every_modelled_form_is_one_django_itself_accepts(source, _reads, _locals):
    """The authority for the grammar is Django's parser, not our examples: if a
    form here is not one Django compiles, the model is describing a language
    nobody writes."""
    from django.template import Engine

    engine = Engine(
        dirs=[], app_dirs=False,
        libraries={"i18n": "django.templatetags.i18n"}, builtins=None,
    )
    engine.from_string("{% load i18n %}" + source)


def test_an_option_word_the_grammar_does_not_know_is_refused_not_guessed():
    """Django raises TemplateSyntaxError on an unknown option. The scanner
    cannot raise (it scans other people's templates too), so it reports the
    construct — which is loud under ``strict`` — rather than quietly turning
    the word into a required context variable, the failure mode this whole
    section replaced."""
    scan = scan_source("{% blocktranslate bogus %}x{% endblocktranslate %}", name="t.html")
    assert scan.variables == {}
    assert scan.unknown_tags == ("blocktranslate(bogus)",)
    with pytest.raises(EmitError, match="unmodelled template construct"):
        scan_source("{% blocktranslate bogus %}x{% endblocktranslate %}", name="t.html",
                    strict=True)


def test_options_do_not_blind_the_scanner_to_the_block_it_opens():
    """The fail-closed half: teaching the parser about options must not turn
    a whole tag into a no-op. Everything the block really reads is still read,
    and only the names the tag itself binds are excluded."""
    scan = scan_source(
        "{% blocktranslate with greeting=salutation count n=items|length "
        'context "mail" trimmed asvar body %}'
        "{{ greeting }}, {{ n }} of {{ total }}"
        "{% endblocktranslate %}{{ body }}",
        name="t.html",
        strict=True,
    )
    assert set(scan.variables) == {"salutation", "items", "total"}


# ── chain resolution ─────────────────────────────────────────────────────────

def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "templates"
    (root / "mail").mkdir(parents=True)
    (root / "mail" / "base.html").write_text(
        "{{ brand }}{% if unsub %}{% include \"mail/_unsub.html\" %}{% endif %}"
        "{% block content %}{% endblock %}"
    )
    (root / "mail" / "_unsub.html").write_text("{{ manage_url }}{{ unsub }}")
    (root / "mail" / "otp.html").write_text(
        '{% extends "mail/base.html" %}{% block content %}{{ code }}{% endblock %}'
    )
    return root


def test_chain_follows_extends_and_include(tmp_path):
    root = _tree(tmp_path)
    chain, variables, _ = resolve_chain("mail/otp.html", [root])
    assert chain == ["mail/otp.html", "mail/base.html", "mail/_unsub.html"]
    assert variables["code"] == "required"
    assert variables["brand"] == "required"


def test_a_guarded_include_contributes_optional_reads(tmp_path):
    """The unsubscribe footer is pulled in only when there IS an unsubscribe
    URL, so an auth letter must not appear to require the footer's
    variables."""
    root = _tree(tmp_path)
    _chain, variables, _ = resolve_chain("mail/otp.html", [root])
    assert variables["manage_url"] == "optional"


def test_a_missing_template_aborts(tmp_path):
    root = _tree(tmp_path)
    (root / "mail" / "broken.html").write_text('{% extends "mail/gone.html" %}')
    with pytest.raises(EmitError, match="not found"):
        resolve_chain("mail/broken.html", [root])


# ── the render call site ─────────────────────────────────────────────────────

CALL_SITE = textwrap.dedent(
    '''
    from django.template.loader import render_to_string

    def send(ctx_key, value, group, user_id, template):
        ctx = {}
        ctx[ctx_key] = value            # dynamic: not resolvable here
        ctx["brand"] = "x"
        ctx.setdefault("company", "y")
        if group != "auth" and user_id:
            ctx["unsub"] = "u"
        if value:
            ctx["host"] = "a"
        else:
            ctx["host"] = "b"
        return render_to_string(template, ctx)
    '''
)


def _site(tmp_path: Path):
    path = tmp_path / "svc.py"
    path.write_text(CALL_SITE)
    return scan_call_site(path, context_var="ctx")


def test_call_site_reads_literal_keys(tmp_path):
    site = _site(tmp_path)
    assert site.variables["brand"]["presence"] == "always"
    assert site.variables["company"]["presence"] == "always"


def test_a_key_written_in_both_branches_is_always(tmp_path):
    site = _site(tmp_path)
    assert site.variables["host"]["presence"] == "always"


def test_a_key_written_under_one_branch_is_conditional_and_carries_its_guard(tmp_path):
    site = _site(tmp_path)
    assert site.variables["unsub"]["presence"] == "conditional"
    assert site.variables["unsub"]["when"] == "group != 'auth' and user_id"


def test_a_computed_key_is_admitted_not_guessed(tmp_path):
    """The one thing static analysis cannot do here is reported as such."""
    site = _site(tmp_path)
    assert site.dynamic_keys is True
    assert "ctx_key" not in site.variables


def test_wiring_that_no_longer_matches_the_code_aborts(tmp_path):
    path = tmp_path / "svc.py"
    path.write_text(CALL_SITE)
    with pytest.raises(EmitError, match="no render_to_string"):
        scan_call_site(path, context_var="other")


# ── the document + its gate ──────────────────────────────────────────────────

def test_undeclared_required_read_aborts_emission(tmp_path):
    root = _tree(tmp_path)
    with pytest.raises(EmitError, match="no provenance declares"):
        build_document(
            module="m",
            version="1",
            routing_key="k",
            template_root="templates",
            template_dirs=[root],
            routes=[Route(key="otp", template="mail/otp.html", context={"a": ["brand"]})],
            call_sites=[],
            limits=[],
        )


def test_a_missing_variable_still_aborts_a_template_full_of_options(tmp_path):
    """The gate must stay fail-closed across the grammar fix. This letter is
    written entirely in the option forms that used to be misread; a variable
    nothing declares still stops emission, and `trimmed` — which the emitter
    once demanded — is not among the things it asks for."""
    root = tmp_path / "templates"
    root.mkdir()
    (root / "letter.html").write_text(
        '{% blocktranslate with who=user.name context "mail" trimmed asvar body %}'
        "Hi {{ who }}, your code is {{ code }}.{% endblocktranslate %}{{ body }}"
    )
    route = Route(key="otp", template="letter.html", context={"caller": ["user", "code"]})

    with pytest.raises(EmitError, match="no provenance declares") as excinfo:
        build_document(
            module="m", version="1", routing_key="k", template_root="templates",
            template_dirs=[root],
            routes=[Route(key="otp", template="letter.html", context={"caller": ["user"]})],
            call_sites=[], limits=[],
        )
    assert "code" in str(excinfo.value)
    assert "trimmed" not in str(excinfo.value)

    doc = build_document(
        module="m", version="1", routing_key="k", template_root="templates",
        template_dirs=[root], routes=[route], call_sites=[], limits=[],
    )
    assert declared_for(doc, "letter.html") == {"user", "code"}


def test_document_is_deterministic_and_declares_its_templates(tmp_path):
    root = _tree(tmp_path)
    route = Route(
        key="otp",
        template="mail/otp.html",
        context={"caller": ["code"], "branding": ["brand"]},
        meta={"group": "auth"},
    )
    first = build_document(
        module="m", version="1", routing_key="k", template_root="templates",
        template_dirs=[root], routes=[route], call_sites=[], limits=["known edge"],
    )
    second = build_document(
        module="m", version="1", routing_key="k", template_root="templates",
        template_dirs=[root], routes=[route], call_sites=[], limits=["known edge"],
    )
    assert first == second
    assert [t["path"] for t in first["templates"]] == [
        "mail/_unsub.html", "mail/base.html", "mail/otp.html",
    ]
    assert first["routes"][0]["group"] == "auth"
    assert first["limits"] == ["known edge"]


def test_declared_for_is_the_hosts_half(tmp_path):
    """What a host gate calls: a path the library no longer ships must raise,
    not return an empty set — an empty set passes a subset check."""
    root = _tree(tmp_path)
    doc = build_document(
        module="m", version="1", routing_key="k", template_root="templates",
        template_dirs=[root],
        routes=[Route(key="otp", template="mail/otp.html",
                      context={"caller": ["code"], "branding": ["brand"]})],
        call_sites=[], limits=[],
    )
    assert "code" in declared_for(doc, "mail/otp.html")
    with pytest.raises(EmitError, match="shadows nothing and is dead code"):
        declared_for(doc, "mail/otp_code.html")
