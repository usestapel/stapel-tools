"""Build/disk lifecycle mechanism for the fleet (``stapel-disk``).

Why this exists
---------------
Twice in one night the owner's machine reached **0 bytes free**. The casualties
were not tests: an in-flight image build died on an opaque ``ENOSPC``, the
OrbStack daemon dropped its socket (every shell tool then failed with an EOF),
and the studio stack went down with it. Measured right after:

* 205 local volumes, 18 active -> **27.9 GB reclaimable (93%)**;
* 99 images, 30 active -> 12.4 GB reclaimable;
* 187 of those volumes unreferenced, **131 of them anonymous PGDATA volumes
  created in a 72-hour window** by a test script that force-removes its
  postgres container without ``-v``, so every run orphans a ~45 MB data
  directory;
* studio's own e2e/smoke runs left ``studio-vol-*`` volumes and
  ``studio-net-*`` networks behind — the manager has a ``destroy()``, nothing
  called it when a run finished.

Every one of those is the same defect with a different name: *something creates
a docker resource and nothing owns its death*. A cleanup script would buy one
night. This module is the mechanism instead, and it lives in ``stapel-tools``
because it applies to the whole fleet — studio, the generated projects, and any
repo whose Makefile builds an image.

Four commands, one rule each
----------------------------
``guard``
    Preflight. Refuse to start a build/sandbox target when free space is below
    a threshold, and say the number: how much is free, how much is required,
    and the exact command that reclaims. A refusal you can act on beats an
    ``ENOSPC`` at layer 7 of 9.

``reclaim``
    Tiered, and explicit about what it will never touch.

    * **Tier 1** (safe, automatic): build cache, dangling images, stopped
      containers. Nothing here is referenced by anything running.
    * **Tier 2** (``--images``, opt-in): unreferenced images. Safe but
      expensive — the next build re-pulls/rebuilds them.
    * **Volumes: never.** ``docker system prune --volumes`` and
      ``docker volume prune`` are *refused by this tool at every tier* and the
      refusal names the reason: the studio's project repositories and project
      databases live in named volumes, and a blanket prune deletes the owner's
      data alongside the garbage. The only volume that this tool will ever
      remove is one that identifies itself as ephemeral — see ``reap``.

``reap``
    The own-garbage reaper. Removes containers/volumes/networks/images that
    carry the ephemeral label (:data:`EPHEMERAL_LABEL`) or match an explicit
    ephemeral name pattern, and **refuses to touch anything else**. Two
    independent guards stand between it and the owner's data: a pattern must be
    specific enough to be a pattern (:func:`assert_safe_pattern`), and every
    individual resource is re-checked against the label/pattern contract before
    removal (:func:`is_ephemeral`). ``--dry-run`` lists without removing.

``doctor``
    Reports the state before it is fatal: free space against the threshold,
    reclaimable bytes per tier, and the count of orphaned ephemeral resources.

The label is the contract
-------------------------
Name patterns are a fallback for resources created before this mechanism
existed. The contract is :data:`EPHEMERAL_LABEL` — ``stapel.ephemeral=true`` —
stamped at creation time by whatever creates a throwaway resource. A durable
project volume and a throwaway e2e volume must be distinguishable by
inspection, not by guessing from a name.

No docker SDK dependency: this shells out to the ``docker`` CLI through one
injectable seam (:class:`Docker`), so the whole module is unit-testable with a
fake and ``stapel-tools`` keeps its empty ``dependencies`` list.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

# ── the contract ────────────────────────────────────────────────────────────
#: Stamped on every throwaway resource at creation. ``true`` means "no data
#: here outlives the run that made me".
EPHEMERAL_LABEL = "stapel.ephemeral"
EPHEMERAL_LABEL_VALUE = "true"
#: Optional second label naming *who* made it (``studio-e2e``, ``import-e2e``),
#: so one run's garbage can be reaped without touching another's.
EPHEMERAL_OWNER_LABEL = "stapel.ephemeral.owner"

#: Fallback name patterns for resources created before the label existed.
#: Every one of these is a shape only a throwaway wears.
DEFAULT_EPHEMERAL_PATTERNS: tuple[str, ...] = (
    "studio-sandbox-*",     # one-shot gate executors (DockerDriver.run_ephemeral)
    "studio-vol-e2e-*",     # e2e project workspace volumes
    "studio-net-e2e-*",
    "studio-vol-smoke-*",   # smoke project workspace volumes
    "studio-net-smoke-*",
    "studio-e2e-*",
    "e2e-*",
)

#: Free space a build/sandbox target requires before it is allowed to start.
#: A studio image is ~1.5 GB and a compose stack pulls several more; below this
#: a full `make e2e` is a coin flip.
DEFAULT_MIN_FREE_GB = 15.0
#: Env override for the threshold, so CI and a laptop can differ without a flag.
MIN_FREE_ENV = "STAPEL_DISK_MIN_FREE_GB"

GIB = 1024 ** 3
#: A pattern must carry at least this many literal characters before its first
#: wildcard. ``*``, ``e*`` and friends are not patterns, they are accidents.
MIN_PATTERN_PREFIX = 4

RECLAIM_COMMAND = "stapel-disk reclaim --images"

_SIZE_RE = re.compile(r"^\s*([0-9.]+)\s*([KMGTP]?i?B|B)?\s*", re.IGNORECASE)
_SIZE_UNITS = {
    "b": 1, "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4, "pb": 1000 ** 5,
    "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4, "pib": 1024 ** 5,
}


def parse_size(text: str) -> int:
    """Bytes from a docker-formatted size (``"12.39GB"``, ``"0B"``, ``"133.5MB"``).

    Docker prints decimal units; unparseable input is 0 rather than an
    exception, because a reporting command must never be the thing that breaks
    a build.
    """
    if not text:
        return 0
    match = _SIZE_RE.match(text)
    if not match:
        return 0
    value = float(match.group(1))
    unit = (match.group(2) or "B").lower()
    return round(value * _SIZE_UNITS.get(unit, 1))


def human(num_bytes: float) -> str:
    """A size a human reads in a refusal message."""
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TiB"


# ── the docker seam ─────────────────────────────────────────────────────────
Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run(argv: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)


@dataclass
class Resource:
    """One docker object the reaper may consider."""

    kind: str            # container | volume | network | image
    ref: str             # id or name used to remove it
    name: str            # human/matchable name
    labels: dict = field(default_factory=dict)
    size: int = 0

    @property
    def is_labelled_ephemeral(self) -> bool:
        return self.labels.get(EPHEMERAL_LABEL, "").lower() == EPHEMERAL_LABEL_VALUE


def parse_labels(raw: str) -> dict:
    """docker's ``{{.Labels}}`` is ``k=v,k=v`` (empty when unlabelled)."""
    out: dict = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        key, _, value = item.partition("=")
        out[key.strip()] = value.strip()
    return out


class Docker:
    """Thin, injectable wrapper over the ``docker`` CLI.

    Every engine call in this module goes through here so tests drive the whole
    mechanism with a fake runner and no daemon.
    """

    def __init__(self, runner: Runner | None = None, *, binary: str = "docker"):
        self._run = runner or _run
        self._binary = binary

    def __call__(self, *args: str) -> "subprocess.CompletedProcess[str]":
        return self._run([self._binary, *args])

    def available(self) -> bool:
        try:
            return self("version", "--format", "{{.Server.Version}}").returncode == 0
        except (OSError, FileNotFoundError):
            return False

    # ── inventory ────────────────────────────────────────────────────────────
    def df(self) -> dict:
        """``{"Images": {...}, "Containers": {...}, ...}`` from ``system df``."""
        proc = self("system", "df", "--format", "json")
        out: dict = {}
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("Type"):
                out[row["Type"]] = row
        return out

    def _table(self, args: Sequence[str], fmt: str) -> list[list[str]]:
        proc = self(*args, "--format", fmt)
        rows = []
        for line in (proc.stdout or "").splitlines():
            if line.strip():
                rows.append(line.split("\t"))
        return rows

    def containers(self, *, running: bool | None = None) -> list[Resource]:
        args: list[str] = ["ps", "-a"]
        if running is False:
            args += ["--filter", "status=exited", "--filter", "status=created",
                     "--filter", "status=dead"]
        elif running is True:
            args += ["--filter", "status=running"]
        return [
            Resource("container", r[0], r[1], parse_labels(r[2] if len(r) > 2 else ""))
            for r in self._table(args, "{{.ID}}\t{{.Names}}\t{{.Labels}}")
        ]

    def volumes(self) -> list[Resource]:
        return [
            Resource("volume", r[0], r[0], parse_labels(r[1] if len(r) > 1 else ""))
            for r in self._table(["volume", "ls"], "{{.Name}}\t{{.Labels}}")
        ]

    def dangling_volumes(self) -> list[Resource]:
        return [
            Resource("volume", r[0], r[0], parse_labels(r[1] if len(r) > 1 else ""))
            for r in self._table(["volume", "ls", "--filter", "dangling=true"],
                                 "{{.Name}}\t{{.Labels}}")
        ]

    def networks(self) -> list[Resource]:
        return [
            Resource("network", r[1], r[1], parse_labels(r[2] if len(r) > 2 else ""))
            for r in self._table(["network", "ls"], "{{.ID}}\t{{.Name}}\t{{.Labels}}")
        ]

    def images(self) -> list[Resource]:
        out = []
        for r in self._table(["image", "ls"],
                             "{{.ID}}\t{{.Repository}}:{{.Tag}}\t{{.Labels}}\t{{.Size}}"):
            out.append(Resource("image", r[0], r[1],
                                parse_labels(r[2] if len(r) > 2 else ""),
                                parse_size(r[3] if len(r) > 3 else "")))
        return out

    # ── removal ──────────────────────────────────────────────────────────────
    def remove(self, resource: Resource) -> tuple[bool, str]:
        if resource.kind == "container":
            # -v: take the anonymous volumes down with the container. Omitting
            # it is precisely the bug that orphaned 131 PGDATA volumes.
            proc = self("rm", "-f", "-v", resource.ref)
        elif resource.kind == "volume":
            proc = self("volume", "rm", "-f", resource.ref)
        elif resource.kind == "network":
            proc = self("network", "rm", resource.ref)
        elif resource.kind == "image":
            proc = self("image", "rm", "-f", resource.ref)
        else:  # pragma: no cover - kinds are a closed set
            return False, f"unknown kind {resource.kind}"
        return proc.returncode == 0, (proc.stderr or "").strip()


# ── 1. preflight guard ──────────────────────────────────────────────────────
@dataclass
class GuardResult:
    ok: bool
    free: int
    total: int
    threshold: int
    path: str
    target: str = ""

    @property
    def shortfall(self) -> int:
        return max(0, self.threshold - self.free)

    def message(self) -> str:
        where = f" before `{self.target}`" if self.target else ""
        if self.ok:
            return (f"disk guard OK{where}: {human(self.free)} free on {self.path} "
                    f"(threshold {human(self.threshold)})")
        return (
            f"DISK GUARD REFUSED{where}: only {human(self.free)} free on {self.path}, "
            f"threshold is {human(self.threshold)} — short by {human(self.shortfall)}.\n"
            f"  A build started here dies mid-layer with an opaque ENOSPC and can take "
            f"the docker daemon's socket with it. Reclaim first:\n"
            f"      {RECLAIM_COMMAND}\n"
            f"      stapel-disk reap --dry-run      # orphaned ephemeral e2e/sandbox resources\n"
            f"      stapel-disk doctor              # where the space actually went\n"
            f"  Override for this run only: --min-free-gb <N> (or {MIN_FREE_ENV}=<N>).\n"
            f"  Volumes are never blanket-pruned by these commands — project repos and "
            f"project databases live in named volumes."
        )


def min_free_bytes(explicit_gb: float | None = None, env: dict | None = None) -> int:
    """Threshold in bytes: flag, else env, else :data:`DEFAULT_MIN_FREE_GB`."""
    if explicit_gb is not None:
        return int(float(explicit_gb) * GIB)
    import os

    raw = (env if env is not None else os.environ).get(MIN_FREE_ENV, "")
    if raw:
        try:
            return int(float(raw) * GIB)
        except ValueError:
            pass
    return int(DEFAULT_MIN_FREE_GB * GIB)


def check_free_space(
    path: str = "/",
    *,
    threshold: int | None = None,
    target: str = "",
    usage: Callable[[str], tuple] | None = None,
) -> GuardResult:
    """Free space on *path* against *threshold* bytes.

    ``usage`` is the injectable :func:`shutil.disk_usage` seam so the guard is
    unit-testable without filling a real disk.
    """
    total, _used, free = (usage or shutil.disk_usage)(path)
    limit = min_free_bytes() if threshold is None else threshold
    return GuardResult(ok=free >= limit, free=free, total=total,
                       threshold=limit, path=path, target=target)


# ── 2. tiered reclaim ───────────────────────────────────────────────────────
#: The refusal, in the tool, in the message — so no future agent or operator
#: reaches for `docker system prune -a --volumes` and calls it housekeeping.
VOLUME_REFUSAL = (
    "REFUSED: this tool never blanket-prunes volumes.\n"
    "  The studio's project repositories (project-repos), its project databases and\n"
    "  every stack's db-data live in NAMED docker volumes. `docker volume prune` and\n"
    "  `docker system prune --volumes` do not distinguish those from build garbage —\n"
    "  they delete the owner's data and report it as reclaimed space.\n"
    "  The only supported volume removal is by identity, not by sweep:\n"
    "      stapel-disk reap --dry-run     # volumes labelled stapel.ephemeral=true\n"
    "                                     # or matching an explicit ephemeral pattern\n"
    "  Anything durable is unreachable from there by construction."
)

TIER1 = "build cache, dangling images, stopped containers"
TIER2 = "unreferenced images"


@dataclass
class ReclaimStep:
    tier: int
    what: str
    argv: tuple[str, ...]
    ok: bool = True
    output: str = ""


def reclaim(
    docker: Docker,
    *,
    images: bool = False,
    volumes: bool = False,
    dry_run: bool = False,
) -> tuple[list[ReclaimStep], str | None]:
    """Run tier 1 (always), tier 2 (``images=True``); refuse volumes, always.

    Returns ``(steps, refusal)``. A non-None refusal means nothing ran.
    """
    if volumes:
        return [], VOLUME_REFUSAL

    steps = [
        ReclaimStep(1, "build cache", ("builder", "prune", "-f")),
        ReclaimStep(1, "stopped containers", ("container", "prune", "-f")),
        ReclaimStep(1, "dangling images", ("image", "prune", "-f")),
    ]
    if images:
        # -a is images, never volumes: `docker image prune -a` cannot reach a
        # volume even in principle. That is why tier 2 is spelled this way and
        # not as `system prune -a`.
        steps.append(ReclaimStep(2, "unreferenced images", ("image", "prune", "-a", "-f")))

    if dry_run:
        return steps, None
    for step in steps:
        proc = docker(*step.argv)
        step.ok = proc.returncode == 0
        step.output = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    return steps, None


# ── 3. the ephemeral reaper ─────────────────────────────────────────────────
class UnsafePattern(ValueError):
    """A pattern broad enough to reach durable resources."""


def assert_safe_pattern(pattern: str) -> str:
    """Refuse a pattern that is not specific enough to be one.

    ``*``, ``std*`` and ``*e2e*`` can all match a durable project volume. A
    pattern must anchor on at least :data:`MIN_PATTERN_PREFIX` literal
    characters before its first wildcard.
    """
    if not pattern or not pattern.strip():
        raise UnsafePattern("empty pattern")
    prefix = re.split(r"[*?\[]", pattern, maxsplit=1)[0]
    if len(prefix) < MIN_PATTERN_PREFIX:
        raise UnsafePattern(
            f"pattern {pattern!r} is too broad — it would match durable resources. "
            f"An ephemeral pattern must start with at least {MIN_PATTERN_PREFIX} "
            f"literal characters (e.g. 'studio-vol-e2e-*')."
        )
    return pattern


def is_ephemeral(
    resource: Resource,
    patterns: Sequence[str],
    *,
    owner: str | None = None,
) -> bool:
    """The single predicate the reaper is allowed to remove on.

    True only when the resource *identifies itself* as throwaway: it carries
    ``stapel.ephemeral=true``, or its name matches an ephemeral pattern. When
    *owner* is given, a labelled resource must also match that owner — one
    run's reaper must not eat a concurrent run's containers.
    """
    if resource.is_labelled_ephemeral:
        if owner and resource.labels.get(EPHEMERAL_OWNER_LABEL) != owner:
            return False
        return True
    if owner:
        # An owner-scoped reap is a label operation; an unlabelled resource has
        # no owner to compare against and stays untouched.
        return False
    return any(fnmatch.fnmatch(resource.name, p) for p in patterns)


@dataclass
class ReapReport:
    matched: list[Resource] = field(default_factory=list)
    removed: list[Resource] = field(default_factory=list)
    failed: list[tuple[Resource, str]] = field(default_factory=list)
    skipped: int = 0        # inspected and left alone — the number that matters

    def counts(self) -> dict:
        out: dict = {}
        for res in self.matched:
            out[res.kind] = out.get(res.kind, 0) + 1
        return out


def collect_ephemeral(
    docker: Docker,
    *,
    patterns: Sequence[str] = DEFAULT_EPHEMERAL_PATTERNS,
    owner: str | None = None,
    kinds: Iterable[str] = ("container", "volume", "network", "image"),
    include_running: bool = True,
) -> ReapReport:
    """Everything that identifies itself as ephemeral, and nothing else."""
    for pattern in patterns:
        assert_safe_pattern(pattern)

    report = ReapReport()
    pool: list[Resource] = []
    kinds = set(kinds)
    if "container" in kinds:
        pool += docker.containers(running=None if include_running else False)
    if "volume" in kinds:
        pool += docker.volumes()
    if "network" in kinds:
        pool += docker.networks()
    if "image" in kinds:
        pool += docker.images()

    for res in pool:
        if is_ephemeral(res, patterns, owner=owner):
            report.matched.append(res)
        else:
            report.skipped += 1
    return report


def reap(
    docker: Docker,
    *,
    patterns: Sequence[str] = DEFAULT_EPHEMERAL_PATTERNS,
    owner: str | None = None,
    kinds: Iterable[str] = ("container", "volume", "network", "image"),
    dry_run: bool = False,
) -> ReapReport:
    """Remove the ephemeral garbage; leave everything else untouched.

    Removal order is containers first: a volume or network still attached to a
    container cannot be removed, and a reaper that reports a failure it caused
    itself is noise.
    """
    report = collect_ephemeral(docker, patterns=patterns, owner=owner, kinds=kinds)
    if dry_run:
        return report

    order = {"container": 0, "image": 1, "volume": 2, "network": 3}
    for res in sorted(report.matched, key=lambda r: order.get(r.kind, 9)):
        # Belt and braces: re-assert the contract immediately before removal,
        # so no refactor of the collection path can widen what gets deleted.
        if not is_ephemeral(res, patterns, owner=owner):  # pragma: no cover - defensive
            continue
        ok, err = docker.remove(res)
        if ok:
            report.removed.append(res)
        else:
            report.failed.append((res, err))
    return report


# ── 4. doctor ───────────────────────────────────────────────────────────────
def doctor(
    docker: Docker,
    *,
    path: str = "/",
    threshold: int | None = None,
    patterns: Sequence[str] = DEFAULT_EPHEMERAL_PATTERNS,
    usage: Callable[[str], tuple] | None = None,
) -> dict:
    """Free space, reclaimable-by-tier and orphan counts as one dict."""
    guard = check_free_space(path, threshold=threshold, usage=usage)
    df = docker.df()

    def _row(key: str) -> dict:
        row = df.get(key) or {}
        return {
            "total": int(row.get("TotalCount") or 0),
            "active": int(row.get("Active") or 0),
            "size": parse_size(row.get("Size", "")),
            "reclaimable": parse_size(row.get("Reclaimable", "")),
        }

    images, containers = _row("Images"), _row("Containers")
    volumes, cache = _row("Local Volumes"), _row("Build Cache")
    orphans = collect_ephemeral(docker, patterns=patterns)

    return {
        "disk": {
            "path": guard.path, "free": guard.free, "total": guard.total,
            "threshold": guard.threshold, "ok": guard.ok,
        },
        "tier1": {
            "what": TIER1,
            "reclaimable": cache["reclaimable"] + containers["reclaimable"],
            "build_cache": cache, "containers": containers,
        },
        "tier2": {"what": TIER2, "reclaimable": images["reclaimable"], "images": images},
        "volumes": {
            "what": "NEVER blanket-pruned (project repos + project databases live here)",
            "total": volumes["total"], "active": volumes["active"],
            "unreferenced_size": volumes["reclaimable"],
        },
        "ephemeral_orphans": orphans.counts(),
        "ephemeral_orphan_total": len(orphans.matched),
    }


def format_doctor(report: dict) -> str:
    disk = report["disk"]
    lines = [
        f"disk {disk['path']}: {human(disk['free'])} free of {human(disk['total'])} "
        f"(threshold {human(disk['threshold'])}) — {'OK' if disk['ok'] else 'BELOW THRESHOLD'}",
        "",
        f"tier 1  {report['tier1']['what']}",
        f"        reclaimable now: {human(report['tier1']['reclaimable'])}"
        f"   (`stapel-disk reclaim`)",
        f"tier 2  {report['tier2']['what']}",
        f"        reclaimable now: {human(report['tier2']['reclaimable'])}"
        f"   (`stapel-disk reclaim --images`)",
        f"volumes {report['volumes']['what']}",
        f"        {report['volumes']['total']} total, {report['volumes']['active']} active, "
        f"{human(report['volumes']['unreferenced_size'])} unreferenced",
        "",
    ]
    orphans = report["ephemeral_orphans"]
    if orphans:
        detail = ", ".join(f"{v} {k}(s)" for k, v in sorted(orphans.items()))
        lines.append(f"ephemeral orphans: {detail}   (`stapel-disk reap --dry-run`)")
    else:
        lines.append("ephemeral orphans: none")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────
def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", default="/", help="filesystem to measure (default: /)")
    parser.add_argument("--min-free-gb", type=float, default=None,
                        help=f"free-space threshold in GiB (default {DEFAULT_MIN_FREE_GB}, "
                             f"env {MIN_FREE_ENV})")


def _add_pattern_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pattern", action="append", default=None, metavar="GLOB",
                        help="ephemeral name pattern (repeatable). Replaces the defaults; "
                             "must start with 4+ literal characters.")
    parser.add_argument("--owner", default=None,
                        help=f"only resources labelled {EPHEMERAL_OWNER_LABEL}=<owner>")
    parser.add_argument("--kind", action="append", default=None,
                        choices=["container", "volume", "network", "image"],
                        help="restrict to a resource kind (repeatable)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stapel-disk",
        description="Build/disk lifecycle for the fleet: preflight guard, tiered "
                    "reclaim (never volumes), ephemeral reaper, doctor.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    guard = sub.add_parser("guard", help="refuse a build/sandbox target below the threshold")
    _add_common(guard)
    guard.add_argument("--for", dest="target", default="",
                       help="name of the target being guarded (quoted in the message)")

    doc = sub.add_parser("doctor", help="free space, reclaimable by tier, orphan counts")
    _add_common(doc)
    doc.add_argument("--json", action="store_true", help="machine-readable output")

    rec = sub.add_parser("reclaim", help="tier 1 always; tier 2 with --images; volumes never")
    rec.add_argument("--images", action="store_true",
                     help="tier 2: also remove unreferenced images")
    rec.add_argument("--volumes", action="store_true",
                     help="refused, always — see the message it prints")
    rec.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")

    rp = sub.add_parser("reap", help="remove ONLY resources that identify themselves ephemeral")
    _add_pattern_args(rp)
    rp.add_argument("--dry-run", action="store_true", help="list matches, remove nothing")
    rp.add_argument("--quiet", action="store_true", help="only print when something is removed")

    return parser


def _patterns_from(args) -> list[str]:
    patterns = list(args.pattern) if args.pattern else list(DEFAULT_EPHEMERAL_PATTERNS)
    for pattern in patterns:
        assert_safe_pattern(pattern)
    return patterns


def main(argv: Sequence[str] | None = None, *, docker: Docker | None = None) -> int:
    args = build_parser().parse_args(argv)
    dock = docker or Docker()

    if args.command == "guard":
        result = check_free_space(args.path, threshold=min_free_bytes(args.min_free_gb),
                                  target=args.target)
        print(result.message(), file=sys.stdout if result.ok else sys.stderr)
        return 0 if result.ok else 1

    if args.command == "doctor":
        if not dock.available():
            print("docker is not reachable — reporting disk only", file=sys.stderr)
            result = check_free_space(args.path, threshold=min_free_bytes(args.min_free_gb))
            print(result.message())
            return 0
        report = doctor(dock, path=args.path, threshold=min_free_bytes(args.min_free_gb))
        print(json.dumps(report, indent=2) if args.json else format_doctor(report))
        return 0

    if args.command == "reclaim":
        steps, refusal = reclaim(dock, images=args.images, volumes=args.volumes,
                                 dry_run=args.dry_run)
        if refusal:
            print(refusal, file=sys.stderr)
            return 2
        for step in steps:
            if args.dry_run:
                print(f"tier {step.tier}: would run `docker {' '.join(step.argv)}`  "
                      f"({step.what})")
            else:
                status = "ok" if step.ok else "FAILED"
                print(f"tier {step.tier} {step.what}: {status}")
                if step.output:
                    print("  " + step.output.replace("\n", "\n  "))
        return 0 if all(s.ok for s in steps) else 1

    if args.command == "reap":
        try:
            patterns = _patterns_from(args)
        except UnsafePattern as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        kinds = args.kind or ["container", "volume", "network", "image"]
        report = reap(dock, patterns=patterns, owner=args.owner, kinds=kinds,
                      dry_run=args.dry_run)
        if args.dry_run:
            for res in report.matched:
                why = "label" if res.is_labelled_ephemeral else "pattern"
                print(f"would remove {res.kind:<9} {res.name}  ({why})")
            print(f"{len(report.matched)} ephemeral resource(s) matched; "
                  f"{report.skipped} inspected and left untouched")
            return 0
        if report.removed or not args.quiet:
            for res in report.removed:
                print(f"reaped {res.kind:<9} {res.name}")
            print(f"reaped {len(report.removed)} ephemeral resource(s); "
                  f"{report.skipped} left untouched")
        for res, err in report.failed:
            print(f"could not remove {res.kind} {res.name}: {err}", file=sys.stderr)
        return 0

    return 2  # pragma: no cover - argparse enforces the closed set


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
