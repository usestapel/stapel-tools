"""``stapel-frontend-repo-init`` — give a SEPARATE frontend repository the
publishing half of the delivery canon.

Why a separate command exists at all
------------------------------------
``stapel-create-project --type monolith`` scaffolds the frontend *inside* the
project, so compose can build it and the §57 canon is complete in one repo.
A microservice project's frontend lives in its OWN repository — that is what
makes it a microservice project — and nothing in this toolchain ever wrote
into that repo. So the delivery canon only ever existed on the CONSUMING side:
the backend declared a volume and a one-shot service, and the frontend repo had
no idea it was supposed to publish anything into them.

That gap is not hypothetical. Measured on ironmemo (2026-08-05):
``ironmemo-frontend`` has no ``Dockerfile`` and no ``.gitlab-ci.yml`` at all —
only a locally built ``dist/``. The backend's nginx served ``root
/frontend-react``, a bind onto a host directory that both
``scripts/deploy_stand.sh`` and the backend's CI explicitly EXCLUDED from
rsync. Nobody could have delivered a build even if they had wanted to. For
months this read as "the frontend does not update" and was diagnosed as
caching.

What it writes
--------------
``Dockerfile``            multi-stage; the ``export`` stage is a dist CARRIER,
                          not an nginx image (the project's own nginx stays the
                          single boundary owning reserved paths, TLS, the proxy
                          table and the cache canon).
``frontend-publish.sh``   the publish step: new build into its own directory,
                          ``current`` repointed, N previous kept so tabs open
                          across a deploy can still fetch their chunks.
``.gitlab-ci.yml`` job    build + push ``sha-<gitsha>``, an IMMUTABLE tag.
   or ``.github/...``     Never ``latest``: with a moving tag "which frontend
                          is on this stand" has no answer and a redeploy
                          silently changes the app. ``stapel-frontend-delivery-
                          lint`` FED002 refuses a mutable tag on the consuming
                          side too.

What it deliberately does NOT do
--------------------------------
It does not bump the tag in the backend repo. The pin lives there, in the env
template, in git — that is what makes the backend↔frontend pair readable from
one repository's history. Automating the bump is a CI-to-CI trigger the two
repos have to agree on; this command only makes the artifact exist.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from ._frontend_templates import (
    DOCKERIGNORE,
    FRONTEND_PUBLISH_SH,
    detect_package_manager,
    render_dockerfile,
)

# GitLab. Mirrors what the backend repos in this fleet already use.
GITLAB_CI_JOB = """\
# ─── Frontend delivery (stapel canon) ───────────────────────────────────────
# Publishes a dist-CARRIER image the backend project's compose pulls by an
# IMMUTABLE tag. See the backend's docker-compose.yml `frontend-build` service
# and scripts/env.stand.template's FRONTEND_TAG.
#
# sha-$CI_COMMIT_SHORT_SHA, never `latest`: a moving tag makes "which frontend
# is on this stand" unanswerable and lets a redeploy silently change the app.
# The backend repo holds the pin, so bumping the frontend is a commit THERE —
# that is what keeps the backend↔frontend pair readable from one history.
publish_frontend:
  stage: build
  image: docker:27
  services:
    - docker:27-dind
  variables:
    IMAGE: "$CI_REGISTRY_IMAGE"
    TAG: "sha-$CI_COMMIT_SHORT_SHA"
  rules:
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
  script:
    - docker login -u "$CI_REGISTRY_USER" -p "$CI_REGISTRY_PASSWORD" "$CI_REGISTRY"
    - docker build --target export -t "$IMAGE:$TAG" .
    - docker push "$IMAGE:$TAG"
    - echo "Pin this in the backend repo — scripts/env.stand.template:"
    - echo "  FRONTEND_IMAGE=$IMAGE"
    - echo "  FRONTEND_TAG=$TAG"
"""

GITHUB_WORKFLOW = """\
# Frontend delivery (stapel canon) — publishes a dist-CARRIER image the backend
# project's compose pulls by an IMMUTABLE tag. sha-<gitsha>, never `latest`:
# a moving tag makes "which frontend is on this stand" unanswerable.
name: Publish frontend image

on:
  push:
    branches: [main]

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Build and push
        run: |
          IMAGE=ghcr.io/${{ github.repository }}
          TAG=sha-$(echo "${{ github.sha }}" | cut -c1-8)
          docker build --target export -t "$IMAGE:$TAG" .
          docker push "$IMAGE:$TAG"
          echo "Pin in the backend repo — scripts/env.stand.template:"
          echo "  FRONTEND_IMAGE=$IMAGE"
          echo "  FRONTEND_TAG=$TAG"
"""

README_SECTION = """\

## Delivery to a stand (stapel canon)

This app is delivered to a stand as a **dist-carrier image**, not as files
copied by the backend's deploy script.

* CI builds `--target export` and pushes `sha-<gitsha>` — an immutable tag.
* The BACKEND repo pins that tag in `scripts/env.stand.template`. Bumping the
  frontend on a stand is a commit in the backend repo; that is what makes
  "which frontend goes with this backend" answerable from one history.
* On deploy, the backend's one-shot `frontend-build` service runs this image,
  which publishes `dist/` into the volume its nginx serves — a new directory
  per build with `current` repointed, keeping the previous builds so a browser
  tab open across the deploy can still fetch its content-hashed chunks.

Do not edit `FRONTEND_TAG` in the stand's `.env`: deploy regenerates `.env`
from the template on every run, so the edit disappears without a word.
"""


def _write(path: Path, content: str, *, force: bool, executable: bool = False) -> str:
    if path.exists() and not force:
        return f"  skipped (exists): {path.name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if executable:
        path.chmod(0o755)
    return f"  wrote: {path.name}"


def init_frontend_repo(repo: Path, *, ci: str = "gitlab", force: bool = False) -> list[str]:
    """Write the publishing half of the delivery canon into *repo*."""
    if not repo.is_dir():
        raise SystemExit(f"frontend-repo-init: not a directory: {repo}")
    if not (repo / "package.json").is_file():
        raise SystemExit(
            f"frontend-repo-init: {repo} has no package.json — this command "
            "writes a build+publish pipeline for a JS frontend repository, and "
            "refuses to scatter one into a directory that is not one."
        )

    # The LOCKFILE decides the install step. `npm ci` in a pnpm repo does not
    # fail loudly — it resolves a DIFFERENT dependency tree than every
    # developer has, and the image you ship stops matching the app anyone
    # tested. ironmemo-frontend is pnpm; the scaffold's own frontend is npm.
    pm = detect_package_manager(repo)
    out = [
        f"  package manager: {pm} (from the lockfile)",
        _write(repo / "Dockerfile", render_dockerfile(pm), force=force),
        _write(repo / ".dockerignore", DOCKERIGNORE, force=force),
        _write(
            repo / "frontend-publish.sh",
            FRONTEND_PUBLISH_SH,
            force=force,
            executable=True,
        ),
    ]

    if ci == "gitlab":
        target = repo / ".gitlab-ci.yml"
        if target.exists() and not force:
            out.append(
                "  .gitlab-ci.yml exists — NOT merged automatically. Append the "
                "publish_frontend job below by hand (a wrong merge into an "
                "existing pipeline is worse than no merge):\n"
                + "\n".join("    " + ln for ln in GITLAB_CI_JOB.splitlines())
            )
        else:
            out.append(_write(target, GITLAB_CI_JOB, force=force))
    elif ci == "github":
        out.append(
            _write(
                repo / ".github" / "workflows" / "publish-frontend.yml",
                GITHUB_WORKFLOW,
                force=force,
            )
        )
    elif ci != "none":
        raise SystemExit(f"frontend-repo-init: unknown --ci {ci!r}")

    readme = repo / "README.md"
    if readme.is_file():
        text = readme.read_text()
        if "Delivery to a stand (stapel canon)" not in text:
            readme.write_text(text.rstrip("\n") + "\n" + README_SECTION)
            out.append("  appended: README.md delivery section")
    return out


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(
        prog="stapel-frontend-repo-init",
        description=(
            "Write the publishing half of the frontend delivery canon "
            "(Dockerfile + publish script + CI job) into a SEPARATE frontend "
            "repository, for a split-repo microservice project."
        ),
    )
    p.add_argument("repo", type=Path, help="path to the frontend repository")
    p.add_argument(
        "--ci",
        default="gitlab",
        choices=("gitlab", "github", "none"),
        help="which CI to write the publish job for (default: gitlab)",
    )
    p.add_argument(
        "--force", action="store_true", help="overwrite files that already exist"
    )
    args = p.parse_args(argv)

    for line in init_frontend_repo(args.repo.resolve(), ci=args.ci, force=args.force):
        print(line, file=sys.stderr)
    print(
        "\nNext: pin the pushed tag in the BACKEND repo's "
        "scripts/env.stand.template (FRONTEND_IMAGE / FRONTEND_TAG). The pin "
        "lives in git, never in the stand's .env — deploy regenerates .env "
        "from the template every run.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
