#!/usr/bin/env python3
"""PPA tools for CasparCG PPA management.

Commands:
  detect-changes  Output a JSON matrix of changed packages for GitHub Actions
  bump            Bump a package to a new upstream version across all distros
  fetch-source    Fetch the upstream orig tarball(s) for a package
  setup-local     Fetch + unpack the source and install build deps, ready for debuild
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

DISTROS = ["generic", "jammy", "noble", "resolute"]

# The Ubuntu series each folder is uploaded to. Packages in 'generic' are
# uploaded once to the oldest series, and the binaries copied to the others in
# Launchpad.
DISTRO_SERIES = {
    "generic": "jammy",
    "jammy": "jammy",
    "noble": "noble",
    "resolute": "resolute",
}

SERIES_IMAGE = {
    "jammy": "ubuntu:22.04",
    "noble": "ubuntu:24.04",
    "resolute": "ubuntu:26.04",
}

NULL_SHA = "0000000000000000000000000000000000000000"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    # Not using git here, as CI containers can refuse to run git in the checkout
    return Path(__file__).resolve().parent.parent


def all_packages(root: Path) -> list[tuple[str, str]]:
    """Return all (distro, package) pairs present in the repo."""
    pairs = []
    for distro in DISTROS:
        distro_dir = root / distro
        if not distro_dir.is_dir():
            continue
        for pkg in sorted(distro_dir.iterdir()):
            if pkg.is_dir() and (pkg / "debian").is_dir():
                pairs.append((distro, pkg.name))
    return pairs


def package_dir(root: Path, distro: str, package: str) -> Path:
    pkg_dir = root / distro / package
    if not (pkg_dir / "debian").is_dir():
        sys.exit(f"Package not found: {distro}/{package}")
    return pkg_dir


def parse_changelog(pkg_dir: Path) -> tuple[str, str, str]:
    """Parse first line of debian/changelog.

    Returns (source_name, full_version, distro_codename).
    e.g. ("casparcg-client", "2.3.1~stable+2-jammy1", "jammy")
    """
    changelog = pkg_dir / "debian" / "changelog"
    first = changelog.read_text().splitlines()[0]
    m = re.match(r"^(\S+)\s+\(([^)]+)\)\s+(\S+);", first)
    if not m:
        raise ValueError(f"Cannot parse changelog first line: {first!r}")
    return m.group(1), m.group(2), m.group(3)


def upstream_version(full_version: str) -> str:
    """Strip the epoch and Debian revision from a full version."""
    return re.sub(r"-[^-]+$", "", re.sub(r"^\d+:", "", full_version))


def make_full_version(up_ver: str, distro: str, n: int = 1) -> str:
    """Build a Debian full version: <upstream>-<distro><n> (or -ubuntu<n> for generic)."""
    suffix = "ubuntu" if distro == "generic" else distro
    return f"{up_ver}-{suffix}{n}"


def prompt_yn(question: str, default: bool = True) -> bool:
    tag = "Y/n" if default else "y/N"
    try:
        ans = input(f"  {question} [{tag}]: ").strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ("y", "yes")


# ---------------------------------------------------------------------------
# Source fetching
# ---------------------------------------------------------------------------

def _source_json(pkg_dir: Path) -> dict | None:
    """Load debian/source.json if present."""
    f = pkg_dir / "debian" / "source.json"
    return json.loads(f.read_text()) if f.exists() else None


def _is_native_package(pkg_dir: Path) -> bool:
    """Return True for 3.0 (native) packages which need no orig tarball."""
    fmt = pkg_dir / "debian" / "source" / "format"
    return fmt.exists() and "native" in fmt.read_text()


def _tarball_ext(path: Path) -> str:
    """Detect the compression of a tarball from its magic bytes."""
    magic = path.read_bytes()[:6]
    if magic.startswith(b"\x1f\x8b"):
        return "gz"
    if magic.startswith(b"BZh"):
        return "bz2"
    if magic.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    raise RuntimeError(f"{path.name} is not a gz, bz2 or xz tarball")


def _download(url: str, dest_dir: Path) -> Path:
    print(f"  Downloading: {url}")
    dest = dest_dir / "download"
    req = urllib.request.Request(url, headers={"User-Agent": "casparcg-ppa-tools"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)
    return dest


def _uscan_download(pkg_dir: Path, src_name: str, dest_dir: Path) -> Path:
    """Run uscan for the current changelog version, returning the orig tarball it made.

    uscan names the tarball after its mangled upstream version, which can differ
    from the changelog (e.g. +N rebuild suffixes), so the caller renames it.
    """
    cmd = ["uscan", "--download-current-version", "--copy", "--destdir", str(dest_dir)]
    # Unauthenticated GitHub API requests are rate limited per IP, which CI runners share
    # (jammy's uscan is too old to support --http-header)
    token = os.environ.get("GITHUB_TOKEN")
    if token and "--http-header" in subprocess.run(["uscan", "--help"], capture_output=True, text=True).stdout:
        cmd += ["--http-header", f"https://api.github.com@Authorization=Bearer {token}"]
    result = subprocess.run(cmd, cwd=pkg_dir, capture_output=True, text=True)
    candidates = sorted(dest_dir.glob(f"{glob.escape(src_name)}_*.orig.tar.*"))
    if not candidates:
        print((result.stdout + result.stderr).strip(), file=sys.stderr)
        raise RuntimeError(f"uscan did not produce an orig tarball for {pkg_dir.name}")
    return candidates[0]


def _fetch_orig(
    pkg_dir: Path, spec: dict, src_name: str, up_ver: str, dest_dir: Path,
    component: str | None,
) -> Path:
    stem = f"{src_name}_{up_ver}.orig" + (f"-{component}" if component else "")
    existing = sorted(dest_dir.glob(f"{glob.escape(stem)}.tar.*"))
    if existing:
        print(f"  Already present: {existing[0].name}")
        return existing[0]

    src_type = spec.get("type", "uscan")
    if src_type == "uscan" and not (pkg_dir / "debian" / "watch").exists():
        raise RuntimeError(f"No debian/watch or debian/source.json for {pkg_dir.name}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        if src_type == "uscan":
            print(f"  Fetching via uscan (version {up_ver})…")
            downloaded = _uscan_download(pkg_dir, src_name, tmp_dir)
        elif src_type == "direct_url":
            downloaded = _download(spec["url"].replace("{version}", up_ver), tmp_dir)
        elif src_type == "github_asset":
            tag = spec.get("tag", "v{version}").replace("{version}", up_ver)
            asset = spec["asset"].replace("{version}", up_ver)
            url = f"https://github.com/{spec['repo']}/releases/download/{tag}/{asset}"
            downloaded = _download(url, tmp_dir)
        else:
            raise RuntimeError(f"Unknown source type {src_type!r} in {pkg_dir}/debian/source.json")

        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{stem}.tar.{_tarball_ext(downloaded)}"
        shutil.move(downloaded, target)

    print(f"  Saved: {target.name}")
    return target


def fetch_source(pkg_dir: Path, dest_dir: Path) -> None:
    """Fetch the upstream orig tarball (and any component tarballs) into dest_dir.

    The source is described by debian/source.json, defaulting to uscan with
    debian/watch. source.json has a 'type' of:
      uscan         — use debian/watch
      direct_url    — 'url'
      github_asset  — 'repo' + 'asset' (+ optional 'tag', default 'v{version}')
    '{version}' is replaced with the upstream version from the changelog.
    An optional 'components' object maps component names to more sources,
    producing <src>_<version>.orig-<component>.tar.* tarballs.
    """
    if _is_native_package(pkg_dir):
        print("  Native package — no orig tarball needed.")
        return

    src_name, full_ver, _ = parse_changelog(pkg_dir)
    up_ver = upstream_version(full_ver)
    spec = _source_json(pkg_dir) or {"type": "uscan"}

    _fetch_orig(pkg_dir, spec, src_name, up_ver, dest_dir, None)
    for component, comp_spec in spec.get("components", {}).items():
        _fetch_orig(pkg_dir, comp_spec, src_name, up_ver, dest_dir, component)


def unpack_source(pkg_dir: Path) -> None:
    """Unpack the orig tarball(s) from the distro folder over the package folder, keeping debian/."""
    if _is_native_package(pkg_dir):
        return
    print("  Unpacking source…")
    subprocess.run(["origtargz", "--unpack"], cwd=pkg_dir, check=True)


# ---------------------------------------------------------------------------
# detect-changes
# ---------------------------------------------------------------------------

def _matrix_entry(distro: str, package: str) -> dict:
    return {"distro": distro, "package": package, "image": SERIES_IMAGE[DISTRO_SERIES[distro]]}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=root)


def cmd_detect_changes(args: argparse.Namespace) -> None:
    root = repo_root()

    if args.only:
        distro, package = args.only
        package_dir(root, distro, package)
        print(json.dumps({"include": [_matrix_entry(distro, package)]}))
        return

    base_ref = args.base_ref
    if not base_ref or base_ref == NULL_SHA or _git(root, "cat-file", "-e", f"{base_ref}^{{commit}}").returncode != 0:
        # New branch or force push: compare against the default branch
        merge_base = _git(root, "merge-base", "HEAD", f"origin/{args.default_branch}")
        if merge_base.returncode != 0:
            print(f"Cannot find a base to compare against; including all packages", file=sys.stderr)
            print(json.dumps({"include": [_matrix_entry(d, p) for d, p in all_packages(root)]}))
            return
        base_ref = merge_base.stdout.strip()

    result = _git(root, "diff", "--name-only", base_ref, "HEAD")
    if result.returncode != 0:
        sys.exit(result.stderr)

    seen: set[tuple[str, str]] = set()
    for f in result.stdout.splitlines():
        parts = Path(f).parts
        if len(parts) >= 2 and parts[0] in DISTROS:
            if (root / parts[0] / parts[1] / "debian").is_dir():
                seen.add((parts[0], parts[1]))

    print(json.dumps({"include": [_matrix_entry(d, p) for d, p in sorted(seen)]}))


# ---------------------------------------------------------------------------
# bump
# ---------------------------------------------------------------------------

def get_patches(pkg_dir: Path) -> list[str]:
    series = pkg_dir / "debian" / "patches" / "series"
    if not series.exists():
        return []
    patches = []
    for line in series.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patches.append(line)
    return patches


def remove_patches(pkg_dir: Path, to_remove: list[str]) -> None:
    series = pkg_dir / "debian" / "patches" / "series"
    lines = series.read_text().splitlines(keepends=True)
    kept = [l for l in lines if l.strip() not in to_remove]
    series.write_text("".join(kept))
    for name in to_remove:
        pf = pkg_dir / "debian" / "patches" / name
        if pf.exists():
            pf.unlink()
            print(f"    Removed patch file: {name}")


def cmd_bump(args: argparse.Namespace) -> None:
    logical = args.package
    new_upstream = args.version
    root = repo_root()

    if "-" in new_upstream:
        sys.exit(
            f"Upstream version can't contain '-', as it separates the debian revision. "
            f"Use '~' for pre-release style suffixes, e.g. {new_upstream.replace('-', '~')}"
        )

    targets = [(d, root / d / p) for d, p in all_packages(root) if p == logical]
    if not targets:
        sys.exit(f"No packages found for '{logical}'")

    print(f"Bumping '{logical}' to upstream version: {new_upstream}")
    print(f"Found in: {', '.join(d for d, _ in targets)}\n")

    env = {
        **os.environ,
        "DEBEMAIL": os.environ.get("DEBEMAIL", "me@julusian.co.uk"),
        "DEBFULLNAME": os.environ.get("DEBFULLNAME", "Julian Waller"),
    }

    for distro, pkg_dir in targets:
        _, cur_ver, _ = parse_changelog(pkg_dir)
        new_full_ver = make_full_version(new_upstream, distro)

        print(f"=== {distro}/{logical} ===")
        print(f"  {cur_ver}  →  {new_full_ver}")

        patches = get_patches(pkg_dir)
        if patches:
            print(f"  Patches ({len(patches)} active):")
            to_remove = [p for p in patches if not prompt_yn(f"Keep patch '{p}'?", default=True)]
            if to_remove:
                remove_patches(pkg_dir, to_remove)

        if (pkg_dir / "debian" / "source.json").exists():
            print("  NOTE: check debian/source.json is correct for the new version")

        message = args.message or f"Update to {new_upstream}"
        subprocess.run(
            [
                "dch",
                "--newversion", new_full_ver,
                "--distribution", DISTRO_SERIES[distro],
                "--force-distribution",
                "--", message,
            ],
            cwd=pkg_dir, env=env, check=True,
        )
        print("  Changelog updated.\n")


# ---------------------------------------------------------------------------
# fetch-source
# ---------------------------------------------------------------------------

def cmd_fetch_source(args: argparse.Namespace) -> None:
    root = repo_root()
    pkg_dir = package_dir(root, args.distro, args.package)
    dest_dir = Path(args.dest).resolve() if args.dest else pkg_dir.parent
    fetch_source(pkg_dir, dest_dir)


# ---------------------------------------------------------------------------
# setup-local
# ---------------------------------------------------------------------------

def cmd_setup_local(args: argparse.Namespace) -> None:
    root = repo_root()
    pkg_dir = package_dir(root, args.distro, args.package)

    src_name, full_ver, _ = parse_changelog(pkg_dir)
    up_ver = upstream_version(full_ver)
    print(f"Setting up {args.distro}/{args.package}  (upstream: {up_ver})\n")

    if args.orig:
        orig = Path(args.orig).expanduser().resolve()
        if not orig.exists():
            sys.exit(f"Tarball not found: {orig}")
        target = pkg_dir.parent / f"{src_name}_{up_ver}.orig.tar.{_tarball_ext(orig)}"
        if orig != target:
            shutil.copyfile(orig, target)
        print(f"  Using provided tarball as {target.name}")

    try:
        fetch_source(pkg_dir, pkg_dir.parent)
    except Exception as e:
        print(f"\n  Could not fetch source automatically:\n    {e}", file=sys.stderr)
        print(
            f"\n  To continue manually, download the upstream tarball and re-run:\n"
            f"    python3 scripts/ppa_tools.py setup-local {args.distro} {args.package}"
            f" --orig /path/to/tarball",
            file=sys.stderr,
        )
        sys.exit(1)

    unpack_source(pkg_dir)

    if not args.skip_deps:
        if shutil.which("mk-build-deps") is None:
            sys.exit("mk-build-deps not found — install devscripts and equivs.")
        print("\nInstalling build dependencies…")
        sudo = [] if os.geteuid() == 0 else ["sudo"]
        # mk-build-deps leaves files in its cwd, which would dirty the source tree
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [
                    *sudo, "mk-build-deps",
                    "-i", "-r",
                    "-t", "apt-get -y --no-install-recommends",
                    str(pkg_dir / "debian" / "control"),
                ],
                cwd=tmp, check=True,
            )

    print(f"\nReady.  Run in  {pkg_dir.relative_to(root)} :")
    print("  debuild -S -uc -us   # source build")
    print("  debuild -b -uc -us   # binary (test) build")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PPA tools for CasparCG PPA",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="command", required=True)

    # detect-changes
    p = subs.add_parser("detect-changes", help="Print JSON matrix of changed packages")
    p.add_argument(
        "--base-ref",
        default="HEAD~1",
        help="Git ref to diff against (default: HEAD~1; pass ${{ github.event.before }} in CI)",
    )
    p.add_argument(
        "--default-branch",
        default="main",
        help="Branch to compare against when --base-ref is missing or unknown (default: main)",
    )
    p.add_argument(
        "--only", nargs=2, metavar=("DISTRO", "PACKAGE"),
        help="Output a matrix for just this package",
    )
    p.set_defaults(func=cmd_detect_changes)

    # bump
    p = subs.add_parser("bump", help="Bump package to a new upstream version in all distros")
    p.add_argument("package", help="Package folder name, e.g. client  server-2.5  media-scanner")
    p.add_argument(
        "version",
        help=(
            "New upstream version as it should appear in the changelog "
            "(e.g. 2.3.2~stable).  The distro suffix is added automatically."
        ),
    )
    p.add_argument("-m", "--message", help="Changelog entry text (default: 'Update to <version>')")
    p.set_defaults(func=cmd_bump)

    # fetch-source
    p = subs.add_parser("fetch-source", help="Fetch upstream orig tarball(s) for one package")
    p.add_argument("distro", choices=DISTROS)
    p.add_argument("package")
    p.add_argument("--dest", metavar="DIR", help="Destination dir (default: <distro>/)")
    p.set_defaults(func=cmd_fetch_source)

    # setup-local
    p = subs.add_parser(
        "setup-local",
        help="Fetch + unpack the source and install build deps, ready for debuild",
        description=(
            "Fetches the orig tarball(s) into <distro>/, unpacks them into the package\n"
            "folder (keeping debian/) and installs the build dependencies.\n\n"
            "If the source can't be fetched automatically, download it yourself and\n"
            "pass it with --orig."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("distro", choices=DISTROS)
    p.add_argument("package")
    p.add_argument(
        "--orig", metavar="TARBALL",
        help="Path to the upstream tarball to use as the main orig tarball",
    )
    p.add_argument("--skip-deps", action="store_true", help="Skip dependency installation")
    p.set_defaults(func=cmd_setup_local)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
