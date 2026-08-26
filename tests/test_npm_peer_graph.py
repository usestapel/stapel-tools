"""``scripts/check_npm_peer_graph.py`` — the pins have to AGREE, not merely exist.

The 0.55.5 CI run died in the e2e on an ERESOLVE while every pin in the
scaffold was published and resolvable, so ``e2e_npm_pins.py`` had nothing to
say: the 17 pair pins had been raised to the 2026-08 release and the substrate
they stand on (``@stapel/core``, ``@stapel/tokens-antd``) had not. A pair
raises its own peer floor in ANOTHER repo, so this cannot be a review rule.

npm is injected here — a test whose verdict changes when someone publishes is
not a test. The live behaviour was verified by hand on 2026-08-27: RED against
the 0.17.0 core pin (35 violations, exit 1), GREEN after the raise (exit 0).
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_npm_peer_graph.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_npm_peer_graph", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _load()


@pytest.fixture(scope="module")
def gate():
    return GATE


# ────────────────────────────── range algebra ──────────────────────────────

@pytest.mark.parametrize(
    "version,spec,expected",
    [
        # The exact forms this fleet publishes.
        ("0.18.1", ">=0.18.1 <1.0.0", True),
        ("0.17.0", ">=0.18.1 <1.0.0", False),   # the 0.55.5 failure, in one line
        ("1.0.0", ">=0.18.1 <1.0.0", False),
        ("0.7.0", ">=0.7.0", True),
        ("0.5.0", ">=0.7.0", False),
        ("6.6.1", ">=5.20.0 <7", True),         # partial upper bound
        ("7.0.0", ">=5.20.0 <7", False),
        ("5.19.9", ">=5.20.0 <7", False),
        ("19.1.0", ">=19", True),               # partial lower bound
        ("18.3.1", ">=19", False),
        ("7.18.2", ">=7.0.0 <8", True),
        ("8.2.0", ">=7.0.0 <8", False),
        ("2.15.0", ">=2 <3", True),
        ("5.102.0", "^5.0.0", True),
        ("6.0.0", "^5.0.0", False),
        # Caret on 0.x: the minor is the major.
        ("0.18.9", "^0.18.1", True),
        ("0.19.0", "^0.18.1", False),
        ("0.18.0", "^0.18.1", False),
        ("0.0.4", "^0.0.3", False),
        ("0.0.3", "^0.0.3", True),
        ("1.9.9", "^1.2.3", True),
        ("2.0.0", "^1.2.3", False),
        # Tilde.
        ("1.2.9", "~1.2.3", True),
        ("1.3.0", "~1.2.3", False),
        ("1.9.9", "~1", True),
        # Exact, wildcard, alternatives.
        ("0.5.1", "0.5.1", True),
        ("0.5.2", "0.5.1", False),
        ("9.9.9", "*", True),
        ("6.6.1", "^5.0.0 || ^6.0.0", True),
        ("7.0.0", "^5.0.0 || ^6.0.0", False),
        # A prerelease only matches a range that names one of the same core.
        ("2.0.0-alpha.1", ">=1.0.0", False),
        ("2.0.0-alpha.1", ">=2.0.0-alpha.0", True),
        ("0.18.2-rc.1", "^0.18.1", False),
    ],
)
def test_satisfies(gate, version, spec, expected):
    assert gate.satisfies(version, spec) is expected


def test_an_unparsable_range_raises_instead_of_passing(gate):
    """A gate that shrugs at what it cannot read is the defect, not the fix."""
    with pytest.raises(gate.RangeSyntaxError):
        gate.satisfies("1.2.3", "1.0.0 - 2.0.0")
    with pytest.raises(gate.RangeSyntaxError):
        gate.satisfies("1.2.3", "npm:other@^1")


def test_version_ordering(gate):
    assert gate.compare("0.18.1", "0.18.1") == 0
    assert gate.compare("0.9.0", "0.10.0") == -1        # not a string compare
    assert gate.compare("1.0.0", "1.0.0-rc.1") == 1     # release outranks its rc
    assert gate.compare("1.0.0-rc.2", "1.0.0-rc.10") == -1


# ─────────────────────────────── the gate ──────────────────────────────────

#: A registry standing in for npm at the moment of the incident. Values are
#: ``(peerDependencies, peerDependenciesMeta)``.
REGISTRY = {
    ("@stapel/auth-react", "0.17.1"): (
        {
            "@stapel/core": ">=0.18.1 <1.0.0",
            "@stapel/tokens-antd": ">=0.7.0",
            "react": ">=19",
        },
        {},
    ),
    ("@stapel/core", "0.17.0"): ({"react": ">=19"}, {}),
    ("@stapel/core", "0.18.1"): ({"react": ">=19"}, {}),
    ("@stapel/tokens-antd", "0.5.0"): ({}, {}),
    ("@stapel/tokens-antd", "0.7.0"): ({}, {}),
    ("react", "19.1.0"): ({}, {}),
}


def fake_npm(registry=REGISTRY, published=None):
    """``npm view <pkg>@<spec> version peerDependencies peerDependenciesMeta``,
    offline.

    Resolution is npm's own — newest PUBLISHED version satisfying the range —
    with the range algebra tested above doing the deciding.
    """
    published = published or {}
    for package, version in registry:
        published.setdefault(package, []).append(version)

    def run(args):
        if args == ["--version"]:
            return 0, "10.9.0"
        _view, target, *_rest = args
        package, _, spec = target.rpartition("@")
        versions = sorted(published.get(package, []), key=lambda v: GATE.parse_version(v))
        if not versions:
            return 1, ""  # E404: npm has never heard of this package
        matches = [v for v in versions if GATE.satisfies(v, spec)]
        if not matches:
            return 0, ""
        best = matches[-1]
        peers, meta = registry[(package, best)]
        return 0, json.dumps({
            "version": best,
            "peerDependencies": peers,
            "peerDependenciesMeta": meta,
        })

    return run


DECLARED_BEFORE = [
    ("@stapel/auth-react", "^0.17.1", "FRONTEND_REACT_LIBS['auth']"),
    ("@stapel/core", "^0.17.0", "FRONTEND_REACT_CORE_DEPS"),
    ("@stapel/tokens-antd", "^0.5.0", "FRONTEND_REACT_ANTD_DEPS"),
    ("react", "^19.1.0", "PACKAGE_JSON.dependencies"),
]

DECLARED_AFTER = [
    ("@stapel/auth-react", "^0.17.1", "FRONTEND_REACT_LIBS['auth']"),
    ("@stapel/core", "^0.18.1", "FRONTEND_REACT_CORE_DEPS"),
    ("@stapel/tokens-antd", "^0.7.0", "FRONTEND_REACT_ANTD_DEPS"),
    ("react", "^19.1.0", "PACKAGE_JSON.dependencies"),
]


def test_a_substrate_left_behind_is_named_with_its_source(gate):
    violations, unresolved, _unpinned, _unpublished = gate.check(
        gate.Registry(fake_npm()), DECLARED_BEFORE,
    )
    assert not unresolved
    offenders = {(v[0], v[2]) for v in violations}
    assert offenders == {
        ("@stapel/auth-react", "@stapel/core"),
        ("@stapel/auth-react", "@stapel/tokens-antd"),
    }
    core = next(v for v in violations if v[2] == "@stapel/core")
    # The report has to name the CONSTANT to edit, not just the packages.
    assert core[3] == ">=0.18.1 <1.0.0"
    assert core[4] == "^0.17.0"
    assert core[5] == "0.17.0"
    assert core[6] == ["FRONTEND_REACT_CORE_DEPS"]


def test_the_raised_substrate_is_green(gate):
    violations, unresolved, _unpinned, unpublished = gate.check(
        gate.Registry(fake_npm()), DECLARED_AFTER,
    )
    assert violations == []
    assert unresolved == []
    assert unpublished == []


def _with_extra_peer(peer, spec, *, optional):
    """A registry where auth-react also peers ``peer``, published or not."""
    peers, meta = REGISTRY[("@stapel/auth-react", "0.17.1")]
    registry = dict(REGISTRY)
    registry[("@stapel/auth-react", "0.17.1")] = (
        {**peers, peer: spec},
        {**meta, **({peer: {"optional": True}} if optional else {})},
    )
    return registry


def test_a_published_peer_the_scaffold_does_not_pin_is_npms_business(gate):
    """npm installs a missing peer itself; only a pin can CONTRADICT one."""
    registry = _with_extra_peer("@stapel/core", ">=0.18.1", optional=False)
    declared = [d for d in DECLARED_AFTER if d[0] != "@stapel/core"]
    violations, _unresolved, unpinned, unpublished = gate.check(
        gate.Registry(fake_npm(registry)), declared,
    )
    assert violations == []
    assert unpublished == []
    assert ("@stapel/auth-react@0.17.1", "@stapel/core", ">=0.18.1") in unpinned


def test_a_required_peer_that_npm_has_never_heard_of_is_named(gate):
    """Verbatim `@stapel/chat-react@0.4.0` -> `@stapel/realtime` (2026-08-27):
    a REQUIRED peer nobody published is an E404 for every project selecting
    that pair, and no pin in this repo can fix it."""
    registry = _with_extra_peer("@stapel/realtime", ">=0.1.1", optional=False)
    violations, _unresolved, unpinned, unpublished = gate.check(
        gate.Registry(fake_npm(registry)), DECLARED_AFTER,
    )
    assert violations == []          # the pin table is not what is wrong
    assert unpinned == []
    assert unpublished == [("@stapel/auth-react@0.17.1", "@stapel/realtime", ">=0.1.1")]


def test_an_optional_unpublished_peer_is_not_named(gate):
    """The two siblings that peer the same unpublished package DO mark it
    optional, and npm installs them without a murmur."""
    registry = _with_extra_peer("@stapel/realtime", ">=0.1.1", optional=True)
    _violations, _unresolved, unpinned, unpublished = gate.check(
        gate.Registry(fake_npm(registry)), DECLARED_AFTER,
    )
    assert unpublished == []
    assert ("@stapel/auth-react@0.17.1", "@stapel/realtime", ">=0.1.1") in unpinned


def test_a_pin_that_resolves_to_nothing_is_reported_not_skipped(gate):
    declared = DECLARED_AFTER + [
        ("@stapel/ghost-react", "^9.9.9", "FRONTEND_REACT_LIBS['ghost']"),
    ]
    violations, unresolved, _unpinned, _unpublished = gate.check(
        gate.Registry(fake_npm()), declared,
    )
    assert violations == []
    assert unresolved == [("@stapel/ghost-react", "^9.9.9", ["FRONTEND_REACT_LIBS['ghost']"])]


# ─────────────────────── it reads the REAL constants ───────────────────────

def test_declarations_cover_every_pin_the_generator_writes(gate):
    """The gate's input is the generator's own tables. A pin added to a new
    constant and not to this list would be invisible — so the coverage is
    asserted against the live tables, not against a copy."""
    from stapel_tools import _frontend_templates as templates
    from stapel_tools import create_project as generator

    declared = gate.pinned_specs(gate.declarations())
    for entry in generator.FRONTEND_REACT_LIBS.values():
        assert f"^{entry['version']}" in declared[entry["package"]]
    for table in (
        generator.FRONTEND_REACT_CORE_DEPS,
        generator.FRONTEND_REACT_ANTD_DEPS,
        generator.FRONTEND_ROUTER_DEPS,
    ):
        for name, version in table.items():
            assert f"^{version}" in declared[name]
    assert f"^{generator.FRONTEND_SHELL_REACT_VERSION}" in declared[generator.FRONTEND_SHELL_REACT_PACKAGE]
    assert f"^{generator.FRONTEND_IMAGE_VERSION}" in declared[generator.FRONTEND_IMAGE_PACKAGE]
    for name, spec in templates.PUBLIC_DEV_DEPS.items():
        assert spec in declared[name]


def test_both_workflows_run_it_before_the_install_it_protects():
    """A gate wired into nothing is this repo's own oldest failure mode
    (check_nav_manifest_sync.py drifted five minors while wired nowhere)."""
    root = Path(__file__).resolve().parents[1]
    for workflow in ("ci.yml", "publish.yml"):
        text = (root / ".github" / "workflows" / workflow).read_text()
        assert "check_npm_peer_graph.py" in text, workflow
        assert text.index("check_npm_peer_graph.py") < text.index("npm install"), workflow


def test_every_declared_spec_is_a_range_this_gate_can_read(gate):
    """Every pin the scaffold writes must be parsable — otherwise the gate is
    green on a range it silently skipped."""
    for package, spec, source in gate.declarations():
        assert gate.satisfies("999.999.999", spec) in (True, False), f"{package} {spec} ({source})"
