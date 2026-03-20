#!/usr/bin/env python3
"""
Collect modifications from a deployed directory back into source repos.

This is the reverse of deploy.py.  Given a directory of deployed MicroPython
packages (e.g. a snapshot produced by deploy.py, mip, or manual installation),
it maps each .py file back to its source location in micropython-lib or a
third-party repo, strips any __version__ trailer, compares with the repo
source, and optionally copies modifications back.

This enables a development workflow where you can:

  1. Deploy packages to a working directory     (deploy.py --output lib ...)
  2. Edit and test in the working directory
  3. Collect changes back into the repos         (collect.py --snapshot lib ...)
  4. Commit the changes in each repo

Usage examples:

    # Show what has been modified (dry-run is the default):
    collect.py --snapshot /path/to/deployed

    # Include unix-ffi packages and third-party repos:
    collect.py --snapshot /path/to/deployed --unix-ffi --repo /path/to/other

    # Handle files installed under a different prefix (e.g. primitives/ was
    # installed into asyncio_extras/primitives/ in the snapshot):
    collect.py --snapshot /path/to/deployed \\
        --remap asyncio_extras/primitives:primitives \\
        --remap asyncio_extras/threadsafe:threadsafe

    # Actually copy the modifications back to the repos:
    collect.py --snapshot /path/to/deployed --write

    # Just list the file-to-source mapping without diffing:
    collect.py --snapshot /path/to/deployed --list
"""

import os
import re
import sys

# ---------------------------------------------------------------------------
# Import deploy.py from the same directory so we can reuse its package
# discovery, manifest parsing, and file-collection infrastructure.
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

import deploy  # noqa: E402


# ===========================================================================
# __version__ stripping
# ===========================================================================


def strip_version(text):
    """
    Remove __version__ = '...' lines that were injected by mip or deploy.py.

    Handles two patterns:
      - Trailing (appended by mip/deploy.py):  \\n\\n__version__ = '...'\\n at EOF
      - Body (artifact from concatenated files): __version__ line between blanks
    """
    text = re.sub(r"\n\n__version__ = '[^']*'\n$", "", text)
    text = re.sub(r"\n__version__ = '[^']*'\n\n", "\n", text)
    return text


# ===========================================================================
# Mapping: deployed target path -> repo source path
# ===========================================================================


def build_target_to_source_map(all_packages):
    """
    Build a dict mapping deployed target paths to their repo source paths.

    Uses deploy.collect_files() to get (source, target) pairs for every
    package, then inverts them.  Returns {target_path: source_path}.
    """
    mapping = {}
    for pkg_name, pkg_info in all_packages.items():
        files = deploy.collect_files(pkg_name, pkg_info)
        for src_path, target_path in files:
            mapping[target_path] = src_path
    return mapping


# ===========================================================================
# Core logic
# ===========================================================================


def find_snapshot_files(snapshot_dir):
    """Yield (relative_path, absolute_path) for every .py file in snapshot_dir."""
    for root, dirs, files in os.walk(snapshot_dir):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            if f.endswith(".py"):
                full = os.path.join(root, f)
                rel = os.path.relpath(full, snapshot_dir)
                yield rel, full


def apply_remaps(path, remaps):
    """
    Apply prefix remapping rules to a snapshot-relative path.

    Each remap is a (snapshot_prefix, target_prefix) tuple.  If *path*
    starts with a snapshot_prefix, it is replaced with target_prefix.
    First match wins.
    """
    for snap_pfx, target_pfx in remaps:
        if path == snap_pfx or path.startswith(snap_pfx + "/"):
            if target_pfx:
                return target_pfx + path[len(snap_pfx):]
            return path[len(snap_pfx) + 1:]  # strip prefix entirely
    return path


def classify_files(snapshot_dir, target_map, remaps):
    """
    Classify every .py file in the snapshot.

    Returns three lists:
      modified:  [(snap_rel, snap_abs, source_path), ...]
      identical: [(snap_rel, source_path), ...]
      unmapped:  [snap_rel, ...]
    """
    modified = []
    identical = []
    unmapped = []

    for snap_rel, snap_abs in find_snapshot_files(snapshot_dir):
        target = apply_remaps(snap_rel, remaps)
        source_path = target_map.get(target)

        if source_path is None:
            unmapped.append(snap_rel)
            continue

        with open(snap_abs, "r", errors="replace") as f:
            snap_content = strip_version(f.read())
        with open(source_path, "r", errors="replace") as f:
            src_content = f.read()

        if snap_content == src_content:
            identical.append((snap_rel, source_path))
        else:
            modified.append((snap_rel, snap_abs, source_path))

    return modified, identical, unmapped


def copy_to_source(snap_abs, source_path):
    """Copy a snapshot file to its repo source, stripping __version__."""
    with open(snap_abs, "r") as f:
        content = strip_version(f.read())
    with open(source_path, "w") as f:
        f.write(content)


# ===========================================================================
# Output helpers
# ===========================================================================


def _c(text, code):
    return deploy.color(text, code)


def print_summary(modified, identical, unmapped, write_mode):
    """Print a summary of the classification results."""
    total = len(modified) + len(identical) + len(unmapped)
    print(f"\n{total} file(s) scanned: "
          f"{_c(str(len(identical)), deploy._COLOR_OK)} identical, "
          f"{_c(str(len(modified)), deploy._COLOR_WARN if modified else deploy._COLOR_OK)} modified, "
          f"{_c(str(len(unmapped)), deploy._COLOR_DIM)} unmapped")

    if not modified:
        print(_c("Nothing to collect.", deploy._COLOR_OK))
        return

    action = "Copied" if write_mode else "Would copy"
    print(f"\n{action} {_c(str(len(modified)), deploy._COLOR_BOLD)} file(s):\n")
    for snap_rel, snap_abs, source_path in modified:
        arrow = _c("->", deploy._COLOR_DIM)
        print(f"  {_c(snap_rel, deploy._COLOR_BOLD)} {arrow} {source_path}")


def print_file_listing(snapshot_dir, target_map, remaps):
    """Print the full file-to-source mapping for every snapshot file."""
    mapped = 0
    unmapped = 0
    for snap_rel, _ in find_snapshot_files(snapshot_dir):
        target = apply_remaps(snap_rel, remaps)
        source = target_map.get(target)
        if source:
            print(f"  {deploy.ljust(snap_rel, 40)} -> {source}")
            mapped += 1
        else:
            print(f"  {deploy.ljust(snap_rel, 40)}    {_c('(unmapped)', deploy._COLOR_DIM)}")
            unmapped += 1
    print(f"\n{mapped} mapped, {unmapped} unmapped")


# ===========================================================================
# Argument parsing
# ===========================================================================


_USAGE = """\
usage: collect.py [-h] --snapshot DIR [--unix-ffi] [--repo DIR]
                  [--remap SNAP_PFX:TARGET_PFX] [--lib-dir DIR]
                  [--write] [-l]

Collect modifications from a deployed directory back into source repos.
This is the reverse of deploy.py.

required:
  --snapshot DIR        Directory containing deployed .py files.

options:
  -h, --help            Show this help message and exit.
  --unix-ffi            Include unix-ffi packages in the mapping.
  --repo DIR            Third-party repo to scan (may be repeated).
  --remap A:B           Map snapshot path prefix A to deploy target prefix B.
                        Use when packages were installed under a non-standard
                        prefix (e.g. --remap asyncio_extras/primitives:primitives).
                        May be specified multiple times.
  --lib-dir DIR         Path to micropython-lib root (default: auto-detected).
  --write               Actually copy modified files back to repos.
                        Without this flag, only a dry-run report is shown.
  -l, --list            List the file-to-source mapping and exit.

examples:
  collect.py --snapshot lib/ --unix-ffi --repo /path/to/micropython-async
  collect.py --snapshot lib/ --remap asyncio_extras/primitives:primitives --write
  collect.py --snapshot lib/ --list
"""


def parse_args(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    class Args:
        snapshot = None
        unix_ffi = False
        repos = []
        remaps = []
        lib_dir = None
        write = False
        list_map = False

    args = Args()
    args.repos = []
    args.remaps = []
    i = 0

    def _need_value(name):
        nonlocal i
        i += 1
        if i >= len(argv):
            print(f"Error: {name} requires a value.", file=sys.stderr)
            sys.exit(2)
        return argv[i]

    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(_USAGE)
            sys.exit(0)
        elif a == "--snapshot":
            args.snapshot = _need_value(a)
        elif a.startswith("--snapshot="):
            args.snapshot = a.split("=", 1)[1]
        elif a == "--unix-ffi":
            args.unix_ffi = True
        elif a == "--repo":
            args.repos.append(_need_value(a))
        elif a.startswith("--repo="):
            args.repos.append(a.split("=", 1)[1])
        elif a == "--remap":
            val = _need_value(a)
            if ":" not in val:
                print(f"Error: --remap value must be A:B, got '{val}'", file=sys.stderr)
                sys.exit(2)
            parts = val.split(":", 1)
            args.remaps.append((parts[0].rstrip("/"), parts[1].rstrip("/")))
        elif a.startswith("--remap="):
            val = a.split("=", 1)[1]
            if ":" not in val:
                print(f"Error: --remap value must be A:B, got '{val}'", file=sys.stderr)
                sys.exit(2)
            parts = val.split(":", 1)
            args.remaps.append((parts[0].rstrip("/"), parts[1].rstrip("/")))
        elif a == "--lib-dir":
            args.lib_dir = _need_value(a)
        elif a.startswith("--lib-dir="):
            args.lib_dir = a.split("=", 1)[1]
        elif a == "--write":
            args.write = True
        elif a in ("-l", "--list"):
            args.list_map = True
        elif a.startswith("-"):
            print(f"Error: unknown option: {a}", file=sys.stderr)
            print("Use -h for help.", file=sys.stderr)
            sys.exit(2)
        else:
            print(f"Error: unexpected argument: {a}", file=sys.stderr)
            sys.exit(2)
        i += 1

    return args


# ===========================================================================
# Entry point
# ===========================================================================


def main():
    args = parse_args()

    if not args.snapshot:
        print("Error: --snapshot is required.", file=sys.stderr)
        print("Use -h for help.", file=sys.stderr)
        sys.exit(2)

    if not os.path.isdir(args.snapshot):
        print(f"Error: snapshot directory not found: '{args.snapshot}'", file=sys.stderr)
        sys.exit(1)

    if args.lib_dir:
        deploy.LIB_DIR = os.path.abspath(args.lib_dir)

    if not os.path.isdir(deploy.LIB_DIR):
        print(f"Error: micropython-lib not found at '{deploy.LIB_DIR}'.", file=sys.stderr)
        sys.exit(1)

    all_packages = deploy.discover_packages(
        deploy.DEFAULT_LIB_DIRS, include_unix_ffi=args.unix_ffi
    )
    for repo_dir in args.repos:
        if not os.path.isdir(repo_dir):
            print(f"Error: repo directory not found: '{repo_dir}'", file=sys.stderr)
            sys.exit(1)
        repo_pkgs = deploy.discover_repo_packages(repo_dir)
        all_packages.update(repo_pkgs)

    target_map = build_target_to_source_map(all_packages)

    if args.list_map:
        print_file_listing(args.snapshot, target_map, args.remaps)
        return

    modified, identical, unmapped = classify_files(
        args.snapshot, target_map, args.remaps
    )

    if args.write and modified:
        for snap_rel, snap_abs, source_path in modified:
            copy_to_source(snap_abs, source_path)

    print_summary(modified, identical, unmapped, write_mode=args.write)


if __name__ == "__main__":
    main()
