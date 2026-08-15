"""
build_release.py — Kick off a release of Investment Tracker.

Usage:
    python build_release.py --version 1.2.0 --notes "Bug fixes and dark mode"

Steps:
    1. Bump APP_VERSION in app.py
    2. Update version.json with the new version + release notes
    3. Commit the version bump and push to main
    4. Push an annotated git tag (e.g. v1.2.0)

GitHub Actions picks up the tag and handles the rest:
    - Builds the PyInstaller bundle on windows-latest
    - Zips the output
    - Creates the GitHub Release and uploads the zip

Requirements:
    - Git configured with push access to the repo
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"\n>> {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f"\nERROR: command failed with exit code {result.returncode}")
        sys.exit(result.returncode)
    return result


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def bump_app_version(version: str):
    path = ROOT / "app.py"
    text = path.read_text(encoding="utf-8")
    updated = re.sub(
        r'^(APP_VERSION\s*=\s*")[^"]*(")',
        rf'\g<1>{version}\g<2>',
        text,
        flags=re.MULTILINE,
    )
    if updated == text:
        print("WARNING: APP_VERSION not found in app.py — skipping bump")
    else:
        path.write_text(updated, encoding="utf-8")
        print(f"  app.py → APP_VERSION = \"{version}\"")


def update_version_json(version: str, notes: str):
    path = ROOT / "version.json"
    data = {
        "version": version,
        "download_url": f"https://github.com/danielczk17/Investment-Tracker/releases/tag/v{version}",
        "release_notes": notes,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  version.json → {version}")


def git_commit_push_tag(version: str, notes: str):
    tag = f"v{version}"
    run(["git", "add", "app.py", "version.json"], cwd=ROOT)
    run(["git", "commit", "-m", f"Release {tag}"], cwd=ROOT)
    run(["git", "push"], cwd=ROOT)
    run(["git", "tag", "-a", tag, "-m", notes], cwd=ROOT)
    run(["git", "push", "origin", tag], cwd=ROOT)
    print(f"\n  Tag {tag} pushed — GitHub Actions will build and publish the release.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Bump version and trigger a GitHub Actions release")
    parser.add_argument("--version", required=True, help="Semantic version, e.g. 1.2.0")
    parser.add_argument("--notes",   required=True, help="Release notes shown in the update banner")
    args = parser.parse_args()

    version = args.version.lstrip("v")
    notes   = args.notes

    print("=" * 56)
    print(f"  Investment Tracker — Release v{version}")
    print("=" * 56)

    print("\n[1/2] Bumping version numbers...")
    bump_app_version(version)
    update_version_json(version, notes)

    print("\n[2/2] Committing, pushing, and tagging...")
    git_commit_push_tag(version, notes)

    print("\n" + "=" * 56)
    print(f"  Done! Watch the build at:")
    print(f"  https://github.com/danielczk17/Investment-Tracker/actions")
    print("=" * 56)


if __name__ == "__main__":
    main()
