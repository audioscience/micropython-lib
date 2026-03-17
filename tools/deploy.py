#!/usr/bin/env python3
"""
Deploy micropython-lib packages to a local directory for the MicroPython Unix port.

The official 'mip' tool downloads pre-compiled .mpy files from the network.
There is no official way to deploy packages from a local micropython-lib source
tree to a directory usable by the Unix port.  This script fills that gap.

It parses manifest.py files (using a lightweight manifest parser),
resolves dependencies, and copies .py source files to a destination directory.

Usage examples:

    # Deploy specific packages (with automatic dependency resolution):
    ./tools/deploy.py --output ~/.micropython/lib logging argparse

    # Deploy all packages from default libraries (python-stdlib, python-ecosys, micropython):
    ./tools/deploy.py --output ~/.micropython/lib --all

    # Include unix-ffi packages too:
    ./tools/deploy.py --output ~/.micropython/lib --all --unix-ffi

    # Deploy specific packages including unix-ffi search path:
    ./tools/deploy.py --output ~/.micropython/lib --unix-ffi os json

    # Dry-run to see what would be installed:
    ./tools/deploy.py --output ~/.micropython/lib --dry-run logging requests

    # List all available packages:
    ./tools/deploy.py --list

    # List packages matching a pattern:
    ./tools/deploy.py --list --filter "hash*"

The output directory should be in MICROPYPATH so MicroPython can find the modules:
    export MICROPYPATH=~/.micropython/lib

"""

import argparse
import fnmatch
import glob
import os
import shutil
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.dirname(SCRIPT_DIR)

DEFAULT_LIB_DIRS = ("micropython", "python-stdlib", "python-ecosys")

_COLOR_OK = "\033[32m"
_COLOR_WARN = "\033[33m"
_COLOR_ERR = "\033[1;31m"
_COLOR_BOLD = "\033[1m"
_COLOR_DIM = "\033[2m"
_COLOR_OFF = "\033[0m"


def color(text, code):
    if sys.stdout.isatty():
        return code + text + _COLOR_OFF
    return text


def discover_packages(lib_dirs, include_unix_ffi=False):
    """
    Scan the micropython-lib tree for all packages with manifest.py files.
    Returns dict: package_name -> {manifest, dir, lib}.
    """
    search_dirs = list(lib_dirs)
    if include_unix_ffi:
        search_dirs.append("unix-ffi")

    packages = {}
    for lib_name in search_dirs:
        lib_path = os.path.join(LIB_DIR, lib_name)
        if not os.path.isdir(lib_path):
            continue
        for manifest_path in glob.glob(
            os.path.join(lib_path, "**", "manifest.py"), recursive=True
        ):
            pkg_dir = os.path.dirname(manifest_path)
            pkg_name = os.path.basename(pkg_dir)
            if pkg_name in packages:
                # unix-ffi should override stdlib for same-named packages
                if lib_name == "unix-ffi":
                    packages[pkg_name] = {
                        "manifest": manifest_path,
                        "dir": pkg_dir,
                        "lib": lib_name,
                    }
            else:
                packages[pkg_name] = {
                    "manifest": manifest_path,
                    "dir": pkg_dir,
                    "lib": lib_name,
                }
    return packages


def parse_manifest(manifest_path):
    """
    Parse a manifest.py extracting metadata, file entries, and dependencies.
    Uses a sandboxed exec with stub functions for the manifest API.

    Returns (metadata_dict, file_entries, dependencies).
    """
    metadata_info = {}
    file_entries = []
    dependencies = []

    class IncludeOptions:
        def __init__(self, **kwargs):
            self._kwargs = kwargs

        def defaults(self, **kwargs):
            pass

        def __getattr__(self, name):
            return self._kwargs.get(name, None)

    def metadata_fn(**kwargs):
        metadata_info.update(kwargs)

    def module_fn(module_path, base_path=".", opt=None):
        file_entries.append(("module", module_path, {"base_path": base_path, "opt": opt}))

    def package_fn(package_path, files=None, base_path=".", opt=None):
        file_entries.append(
            ("package", package_path, {"files": files, "base_path": base_path, "opt": opt})
        )

    def require_fn(name, version=None, pypi=None, library=None, **kwargs):
        dependencies.append(name)

    def include_fn(path, **kwargs):
        pass

    def add_library_fn(library, library_path, prepend=False):
        pass

    manifest_globals = {
        "metadata": metadata_fn,
        "include": include_fn,
        "require": require_fn,
        "add_library": add_library_fn,
        "package": package_fn,
        "module": module_fn,
        "options": IncludeOptions(),
    }

    with open(manifest_path, "r") as f:
        code = f.read()

    exec(compile(code, manifest_path, "exec"), manifest_globals)
    return metadata_info, file_entries, dependencies


def resolve_dependencies(package_names, all_packages, resolved=None, resolving=None):
    """
    Recursively resolve dependencies for the given package names.
    Returns an ordered list of package names (dependencies first).
    """
    if resolved is None:
        resolved = []
    if resolving is None:
        resolving = set()

    for name in package_names:
        if name in resolved:
            continue
        if name not in all_packages:
            print(
                color("Warning:", _COLOR_WARN),
                "Package '{}' not found, skipping.".format(name),
            )
            continue
        if name in resolving:
            continue

        resolving.add(name)
        pkg = all_packages[name]
        _, _, deps = parse_manifest(pkg["manifest"])
        resolve_dependencies(deps, all_packages, resolved, resolving)
        resolving.discard(name)

        if name not in resolved:
            resolved.append(name)

    return resolved


def collect_files(pkg_name, pkg_info):
    """
    Collect all .py files that need to be copied for a package.
    Returns list of (src_path, target_path) tuples.
    """
    _, file_entries, _ = parse_manifest(pkg_info["manifest"])
    pkg_dir = pkg_info["dir"]
    result = []

    for entry_type, name, kwargs in file_entries:
        base_path = kwargs.get("base_path", ".")
        if base_path == ".":
            base_path = pkg_dir
        else:
            base_path = os.path.join(pkg_dir, base_path)

        if entry_type == "module":
            src = os.path.join(base_path, name)
            if os.path.exists(src):
                result.append((src, name))
            else:
                print(
                    color("Warning:", _COLOR_WARN),
                    "File '{}' not found in package '{}'.".format(src, pkg_name),
                )

        elif entry_type == "package":
            pkg_src_dir = os.path.join(base_path, name)
            specified_files = kwargs.get("files")

            if specified_files:
                for rel_file in specified_files:
                    src = os.path.join(pkg_src_dir, rel_file)
                    target = os.path.join(name, rel_file)
                    if os.path.exists(src):
                        result.append((src, target))
                    else:
                        print(
                            color("Warning:", _COLOR_WARN),
                            "File '{}' not found in package '{}'.".format(src, pkg_name),
                        )
            else:
                if os.path.isdir(pkg_src_dir):
                    for root, dirs, files in os.walk(pkg_src_dir):
                        dirs.sort()
                        for f in sorted(files):
                            if f.endswith(".py"):
                                src = os.path.join(root, f)
                                rel = os.path.relpath(src, base_path)
                                result.append((src, rel))
                else:
                    print(
                        color("Warning:", _COLOR_WARN),
                        "Package directory '{}' not found for '{}'.".format(
                            pkg_src_dir, pkg_name
                        ),
                    )

    return result


def deploy_packages(package_names, all_packages, output_dir, dry_run=False):
    """Deploy resolved packages to output_dir. Returns (pkg_count, file_count)."""
    total_files = 0
    total_packages = 0

    for pkg_name in package_names:
        if pkg_name not in all_packages:
            continue

        pkg_info = all_packages[pkg_name]
        files = collect_files(pkg_name, pkg_info)
        metadata, _, _ = parse_manifest(pkg_info["manifest"])
        version = metadata.get("version", "")

        if not files:
            print(
                "  {} {} {}".format(
                    color("skip", _COLOR_DIM),
                    pkg_name,
                    color("(no files)", _COLOR_DIM),
                )
            )
            continue

        action = color("would install", _COLOR_WARN) if dry_run else color("install", _COLOR_OK)
        ver_str = color("@" + version, _COLOR_DIM) if version else ""
        print(
            "  {} {}{} [{} file(s)]".format(
                action,
                color(pkg_name, _COLOR_BOLD),
                ver_str,
                len(files),
            )
        )

        for src, target in files:
            dest = os.path.join(output_dir, target)
            if dry_run:
                print("    {} -> {}".format(color(target, _COLOR_DIM), dest))
            else:
                dest_dir = os.path.dirname(dest)
                if dest_dir and not os.path.isdir(dest_dir):
                    os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(src, dest)

        total_files += len(files)
        total_packages += 1

    return total_packages, total_files


def list_packages(all_packages, filter_pattern=None):
    """Print a formatted list of all available packages."""
    names = sorted(all_packages.keys())
    if filter_pattern:
        names = [n for n in names if fnmatch.fnmatch(n, filter_pattern)]

    if not names:
        msg = "No packages found"
        if filter_pattern:
            msg += " matching '{}'".format(filter_pattern)
        print(msg + ".")
        return

    max_name = max(len(n) for n in names)
    max_lib = max(len(all_packages[n]["lib"]) for n in names)

    print("{} {} {} {}".format(
        "Package".ljust(max_name),
        "Library".ljust(max_lib),
        "Version".ljust(10),
        "Dependencies",
    ))
    print("-" * (max_name + max_lib + 30))

    for name in names:
        pkg = all_packages[name]
        metadata, _, deps = parse_manifest(pkg["manifest"])
        version = metadata.get("version", "")
        dep_str = ", ".join(deps) if deps else ""
        print(
            "{} {} {} {}".format(
                color(name.ljust(max_name), _COLOR_BOLD),
                pkg["lib"].ljust(max_lib),
                (version or "").ljust(10),
                color(dep_str, _COLOR_DIM),
            )
        )

    print("\n{} package(s) found.".format(len(names)))


def main():
    parser = argparse.ArgumentParser(
        description="Deploy micropython-lib packages to a local directory for the Unix port.",
        epilog=(
            "Examples:\n"
            "  %(prog)s --output ~/.micropython/lib logging argparse\n"
            "  %(prog)s --output ~/.micropython/lib --all\n"
            "  %(prog)s --output ~/.micropython/lib --all --unix-ffi\n"
            "  %(prog)s --list\n"
            "  %(prog)s --list --filter 'hash*'\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "packages",
        nargs="*",
        help="Package names to install (use --all for everything).",
    )
    parser.add_argument(
        "--output", "-o",
        help="Destination directory for deployed packages.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Deploy all available packages.",
    )
    parser.add_argument(
        "--unix-ffi",
        action="store_true",
        help="Include unix-ffi packages (overrides stdlib equivalents for same-named packages).",
    )
    parser.add_argument(
        "--no-deps",
        action="store_true",
        help="Do not install dependencies automatically.",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be installed without copying files.",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        dest="list_pkgs",
        help="List all available packages and exit.",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Filter pattern for --list (shell glob, e.g. 'hash*').",
    )
    parser.add_argument(
        "--lib-dir",
        default=None,
        help="Path to micropython-lib root (default: auto-detected from script location).",
    )

    args = parser.parse_args()

    global LIB_DIR
    if args.lib_dir:
        LIB_DIR = os.path.abspath(args.lib_dir)

    if not os.path.isdir(LIB_DIR):
        print("Error: micropython-lib not found at '{}'.".format(LIB_DIR), file=sys.stderr)
        sys.exit(1)

    all_packages = discover_packages(DEFAULT_LIB_DIRS, include_unix_ffi=args.unix_ffi)

    if args.list_pkgs:
        list_packages(all_packages, args.filter)
        return

    if not args.output:
        parser.error("--output is required when installing packages.")

    if not args.all and not args.packages:
        parser.error("Specify package names or use --all.")

    output_dir = os.path.abspath(args.output)

    if args.all:
        requested = sorted(all_packages.keys())
    else:
        requested = args.packages

    unknown = [p for p in requested if p not in all_packages]
    if unknown:
        print(
            color("Error:", _COLOR_ERR),
            "Unknown package(s): {}".format(", ".join(unknown)),
            file=sys.stderr,
        )
        print("Use --list to see available packages.", file=sys.stderr)
        sys.exit(1)

    if args.no_deps:
        resolved = requested
    else:
        resolved = resolve_dependencies(requested, all_packages)

    dep_count = len(resolved) - len(requested) if not args.all else 0
    print(
        "Deploying {} package(s){} to {}{}".format(
            len(resolved),
            " ({} deps)".format(dep_count) if dep_count > 0 else "",
            output_dir,
            color(" (dry run)", _COLOR_WARN) if args.dry_run else "",
        )
    )

    if not args.dry_run:
        os.makedirs(output_dir, exist_ok=True)

    n_pkgs, n_files = deploy_packages(resolved, all_packages, output_dir, dry_run=args.dry_run)

    summary_verb = "Would deploy" if args.dry_run else "Deployed"
    print("\n{} {} package(s), {} file(s).".format(summary_verb, n_pkgs, n_files))

    if not args.dry_run and n_pkgs > 0:
        print(
            "\nTo use with MicroPython Unix port, ensure MICROPYPATH includes this directory:"
        )
        print("  export MICROPYPATH={}".format(output_dir))


if __name__ == "__main__":
    main()
