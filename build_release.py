"""
build_release.py — End-to-end release script for Investment Tracker.

Usage:
    python build_release.py --version 1.2.0 --notes "Bug fixes and dark mode"

Steps:
    1. Bump APP_VERSION in app.py
    2. Update version.json with the new version + release notes
    3. Build the PyInstaller bundle (dist\InvestmentTracker\)
    4. Zip the bundle into InvestmentTracker-v<version>-windows.zip
    5. Commit the version bump to git
    6. Push to GitHub
    7. Create a GitHub release and upload the zip as an asset

Requirements:
    - Python 3.7+
    - PyInstaller:  pip install pyinstaller
    - GitHub CLI:   https://cli.github.com  (must be logged in: gh auth login)
"""

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, print it, and abort on failure."""
    print(f"\n>> {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f"\nERROR: command failed with exit code {result.returncode}")
        sys.exit(result.returncode)
    return result


def check_gh_installed():
    if shutil.which("gh") is None:
        print("ERROR: GitHub CLI (gh) is not installed or not in PATH.")
        print("Download it from https://cli.github.com and run: gh auth login")
        sys.exit(1)


def check_pyinstaller_installed():
    if shutil.which("pyinstaller") is None:
        print("ERROR: PyInstaller is not installed.")
        print("Run: pip install pyinstaller")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def bump_app_version(version: str):
    """Replace APP_VERSION = "x.y.z" in app.py."""
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
    """Write version.json with the new version and release notes."""
    import json
    path = ROOT / "version.json"
    data = {
        "version": version,
        "download_url": f"https://github.com/danielczk17/Investment-Tracker/releases/tag/v{version}",
        "release_notes": notes,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  version.json → {version}")


def build_pyinstaller():
    """Clean previous build and run PyInstaller."""
    for folder in ("build", "dist"):
        target = ROOT / folder
        if target.exists():
            shutil.rmtree(target)
            print(f"  Removed {folder}/")

    run(
        ["pyinstaller", "investment_tracker.spec", "--noconfirm"],
        cwd=ROOT,
    )


def create_zip(version: str) -> Path:
    """Zip dist\InvestmentTracker\ into InvestmentTracker-v<version>-windows.zip."""
    src  = ROOT / "dist" / "InvestmentTracker"
    dest = ROOT / f"InvestmentTracker-v{version}-windows.zip"

    if dest.exists():
        dest.unlink()

    print(f"\n  Zipping {src} → {dest.name}")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in src.rglob("*"):
            zf.write(file, file.relative_to(src.parent))

    size_mb = dest.stat().st_size / 1_048_576
    print(f"  Done — {dest.name} ({size_mb:.1f} MB)")
    return dest


def git_commit_and_push(version: str):
    """Commit the version bump and push to main."""
    run(["git", "add", "app.py", "version.json"], cwd=ROOT)
    run(
        ["git", "commit", "-m", f"Release v{version}"],
        cwd=ROOT,
    )
    run(["git", "push"], cwd=ROOT)


def create_github_release(version: str, notes: str, zip_path: Path):
    """Create a GitHub release and upload the zip."""
    tag = f"v{version}"
    run(
        [
            "gh", "release", "create", tag,
            str(zip_path),
            "--title", f"Investment Tracker {tag}",
            "--notes", notes,
        ],
        cwd=ROOT,
    )
    print(f"\n  Release published: https://github.com/danielczk17/Investment-Tracker/releases/tag/{tag}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Build and release Investment Tracker")
    parser.add_argument("--version", required=True, help="Semantic version, e.g. 1.2.0")
    parser.add_argument("--notes",   required=True, help="Release notes shown in the update banner")
    parser.add_argument(
        "--skip-build", action="store_true",
        help="Skip PyInstaller (use existing dist folder — for testing the release step only)",
    )
    args = parser.parse_args()

    version = args.version.lstrip("v")   # accept both "1.2.0" and "v1.2.0"
    notes   = args.notes

    print("=" * 56)
    print(f"  Investment Tracker — Release v{version}")
    print("=" * 56)

    check_gh_installed()
    if not args.skip_build:
        check_pyinstaller_installed()

    # Step 1 & 2 — version bumps
    print("\n[1/5] Bumping version numbers...")
    bump_app_version(version)
    update_version_json(version, notes)

    # Step 3 — build
    if args.skip_build:
        print("\n[2/5] Skipping build (--skip-build)")
    else:
        print("\n[2/5] Building with PyInstaller...")
        build_pyinstaller()

    # Step 4 — zip
    print("\n[3/5] Creating zip archive...")
    zip_path = create_zip(version)

    # Step 5 — git commit + push
    print("\n[4/5] Committing version bump and pushing...")
    git_commit_and_push(version)

    # Step 6 — GitHub release
    print("\n[5/5] Creating GitHub release...")
    create_github_release(version, notes, zip_path)

    print("\n" + "=" * 56)
    print(f"  Done! v{version} is live on GitHub.")
    print(f"  Existing users will see the update banner automatically.")
    print("=" * 56)


if __name__ == "__main__":
    main()
