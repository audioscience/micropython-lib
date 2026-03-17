#!/usr/bin/env python3
"""
Deploy micropython-lib packages to a local directory for the MicroPython Unix port.

Compatible with both CPython 3 and MicroPython (Unix port).

The official 'mip' tool downloads pre-compiled .mpy files from the network.
There is no official way to deploy packages from a local micropython-lib source
tree to a directory usable by the Unix port.  This script fills that gap.

It parses manifest.py files and package.json files (the two packaging formats
used in the MicroPython ecosystem), resolves dependencies, and copies .py source
files to a destination directory.

Usage examples:

    # Deploy specific packages (with automatic dependency resolution):
    deploy.py --output ~/.micropython/lib logging argparse

    # Deploy all packages from default libraries (python-stdlib, python-ecosys, micropython):
    deploy.py --output ~/.micropython/lib --all

    # Include unix-ffi packages too:
    deploy.py --output ~/.micropython/lib --all --unix-ffi

    # Deploy from a cloned third-party repo (supports manifest.py and package.json):
    deploy.py --output ~/.micropython/lib --repo /path/to/micropython-async primitives

    # Combine micropython-lib and third-party repos:
    deploy.py --output ~/.micropython/lib --repo /path/to/repo1 --repo /path/to/repo2 --all

    # Dry-run to see what would be installed:
    deploy.py --output ~/.micropython/lib --dry-run logging requests

    # List all available packages:
    deploy.py --list

    # List packages from a third-party repo:
    deploy.py --list --repo /path/to/micropython-async

    # List packages matching a pattern:
    deploy.py --list --filter "hash*"

The output directory should be in MICROPYPATH so MicroPython can find the modules:
    export MICROPYPATH=~/.micropython/lib
"""

import json
import os
import sys


# ===========================================================================
# Portable stdlib shims
# ===========================================================================
# Each block tries CPython's stdlib first, then falls back to a minimal
# implementation sufficient for this script.  Fallbacks use only builtins
# available in MicroPython's Unix port (os.listdir, os.stat, os.mkdir,
# os.getcwd, open).  Function names match their Python 3 stdlib equivalents.

# ---- os.path ----

try:
    from os.path import abspath, basename, dirname, exists, isdir, join, relpath
except ImportError:

    def join(*parts):
        """os.path.join -- combine path components."""
        result = ""
        for p in parts:
            if p.startswith("/"):
                result = p
            elif not result or result.endswith("/"):
                result += p
            else:
                result += "/" + p
        return result

    def dirname(path):
        """os.path.dirname -- directory component of path."""
        idx = path.rfind("/")
        if idx < 0:
            return ""
        if idx == 0:
            return "/"
        return path[:idx]

    def basename(path):
        """os.path.basename -- final component of path."""
        idx = path.rfind("/")
        if idx < 0:
            return path
        return path[idx + 1:]

    def exists(path):
        """os.path.exists -- True if path exists."""
        try:
            os.stat(path)
            return True
        except OSError:
            return False

    def isdir(path):
        """os.path.isdir -- True if path is a directory."""
        try:
            return os.stat(path)[0] & 0o040000 != 0
        except OSError:
            return False

    def abspath(path):
        """os.path.abspath -- return absolute version of path."""
        if not path.startswith("/"):
            path = os.getcwd() + "/" + path
        parts = path.split("/")
        normalized = []
        for p in parts:
            if p == "" or p == ".":
                if not normalized:
                    normalized.append("")
            elif p == "..":
                if len(normalized) > 1:
                    normalized.pop()
            else:
                normalized.append(p)
        return "/".join(normalized) or "/"

    def relpath(path, start="."):
        """os.path.relpath -- compute relative path from start to path."""
        path = abspath(path)
        start = abspath(start)
        path_parts = [p for p in path.split("/") if p]
        start_parts = [p for p in start.split("/") if p]
        i = 0
        while (
            i < len(path_parts)
            and i < len(start_parts)
            and path_parts[i] == start_parts[i]
        ):
            i += 1
        ups = len(start_parts) - i
        remainder = path_parts[i:]
        if not ups and not remainder:
            return "."
        return "/".join([".."] * ups + remainder)


# ---- os.walk ----

try:
    from os import walk
except (ImportError, AttributeError):

    def walk(top, topdown=True):
        """os.walk -- recursive directory tree generator."""
        try:
            names = sorted(os.listdir(top))
        except OSError:
            return
        dirs, files = [], []
        for name in names:
            full = join(top, name)
            try:
                if os.stat(full)[0] & 0o040000:
                    dirs.append(name)
                else:
                    files.append(name)
            except OSError:
                files.append(name)
        if topdown:
            yield top, dirs, files
        for d in dirs:
            yield from walk(join(top, d), topdown=topdown)
        if not topdown:
            yield top, dirs, files


# ---- os.makedirs ----

try:
    from os import makedirs
except (ImportError, AttributeError):

    def makedirs(path, exist_ok=False):
        """os.makedirs -- recursive directory creation."""
        path = abspath(path)
        parts = path.split("/")
        current = ""
        for part in parts:
            if not current:
                current = part or "/"
            else:
                current = current.rstrip("/") + "/" + part
            if current == "/":
                continue
            try:
                os.mkdir(current)
            except OSError:
                if not exist_ok and not isdir(current):
                    raise


# ---- shutil.copy2 ----

try:
    from shutil import copy2
except ImportError:

    def copy2(src, dst):
        """shutil.copy2 -- copy file contents (metadata preservation best-effort)."""
        with open(src, "rb") as f_in:
            data = f_in.read()
        with open(dst, "wb") as f_out:
            f_out.write(data)


# ---- fnmatch.fnmatch ----

try:
    from fnmatch import fnmatch
except ImportError:

    def fnmatch(name, pattern):
        """fnmatch.fnmatch -- Unix shell-style pattern matching (* and ?)."""
        return _fnmatch_impl(name, pattern, 0, 0)

    def _fnmatch_impl(name, pattern, ni, pi):
        while pi < len(pattern):
            pc = pattern[pi]
            if pc == "*":
                while pi < len(pattern) and pattern[pi] == "*":
                    pi += 1
                if pi == len(pattern):
                    return True
                for ni2 in range(ni, len(name) + 1):
                    if _fnmatch_impl(name, pattern, ni2, pi):
                        return True
                return False
            elif pc == "?":
                if ni >= len(name):
                    return False
                ni += 1
                pi += 1
            else:
                if ni >= len(name) or name[ni] != pc:
                    return False
                ni += 1
                pi += 1
        return ni == len(name)


# ---- str.ljust ----

def ljust(s, width, fillchar=" "):
    """str.ljust -- left-justify string in a field of given width."""
    pad = width - len(s)
    if pad > 0:
        return s + fillchar * pad
    return s


# ===========================================================================
# Constants
# ===========================================================================

SCRIPT_DIR = dirname(abspath(__file__))
LIB_DIR = dirname(SCRIPT_DIR)

DEFAULT_LIB_DIRS = ("micropython", "python-stdlib", "python-ecosys")

_COLOR_OK = "\033[32m"
_COLOR_WARN = "\033[33m"
_COLOR_ERR = "\033[1;31m"
_COLOR_BOLD = "\033[1m"
_COLOR_DIM = "\033[2m"
_COLOR_OFF = "\033[0m"


def _use_color():
    try:
        return sys.stdout.isatty()
    except AttributeError:
        return False


_USE_COLOR = _use_color()


def color(text, code):
    if _USE_COLOR:
        return f"{code}{text}{_COLOR_OFF}"
    return text


# ===========================================================================
# Recursive file finder (replaces glob.glob with recursive=True)
# ===========================================================================


def find_files(directory, filename):
    """Recursively find all files named *filename* under *directory*."""
    results = []
    for root, _dirs, files in walk(directory):
        if filename in files:
            results.append(join(root, filename))
    return results


# ===========================================================================
# Manifest parsing -- manifest.py (micropython-lib native format)
# ===========================================================================


def parse_manifest(manifest_path):
    """
    Parse a manifest.py, extracting metadata, file entries, and dependencies.
    Uses a sandboxed exec with stub functions for the manifest API.

    Returns (metadata_dict, file_entries, dependencies).
    file_entries: list of ("module"|"package", name, kwargs).
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


# ===========================================================================
# Manifest parsing -- package.json (mip / third-party format)
# ===========================================================================


def _resolve_pkg_json_source(source_url, pkg_dir, repo_root):
    """
    Resolve a package.json source URL to a local filesystem path.

    Handles:
      - github:org/repo/path  -> repo_root/path
      - gitlab:org/repo/path  -> repo_root/path
      - relative/path         -> pkg_dir/relative/path
      - http(s) URLs          -> None (cannot resolve locally)
    """
    for prefix in ("github:", "gitlab:"):
        if source_url.startswith(prefix):
            path_after = source_url[len(prefix):]
            parts = path_after.split("/", 2)
            if len(parts) >= 3:
                return join(repo_root, parts[2])
            return None
    if source_url.startswith("http://") or source_url.startswith("https://"):
        return None
    return join(pkg_dir, source_url)


def parse_package_json(json_path, repo_root):
    """
    Parse a package.json file (mip format).

    Returns (metadata_dict, file_entries, dependencies) -- same shape as
    parse_manifest() so callers can treat both formats uniformly.

    file_entries: list of ("url_file", target_path, {"source": local_path}).
    """
    with open(json_path, "r") as f:
        data = json.load(f)

    pkg_dir = dirname(json_path)
    metadata = {"version": data.get("version", "")}
    file_entries = []
    dependencies = []

    for entry in data.get("urls", []):
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        target, source_url = entry[0], entry[1]
        local_src = _resolve_pkg_json_source(source_url, pkg_dir, repo_root)
        if local_src is not None:
            file_entries.append(("url_file", target, {"source": local_src}))

    for dep_entry in data.get("deps", []):
        if not isinstance(dep_entry, list) or len(dep_entry) < 1:
            continue
        dep_name = dep_entry[0]
        for prefix in ("github:", "gitlab:"):
            if dep_name.startswith(prefix):
                # github:org/repo -> use repo basename as package name
                parts = dep_name[len(prefix):].rstrip("/").split("/")
                dep_name = parts[-1] if parts else dep_name
                break
        dependencies.append(dep_name)

    return metadata, file_entries, dependencies


# ===========================================================================
# Unified parse dispatcher
# ===========================================================================


def parse_package(pkg_info):
    """
    Parse package metadata regardless of format.
    Dispatches to parse_manifest() or parse_package_json() based on the
    'format' key in pkg_info.

    Returns (metadata_dict, file_entries, dependencies).
    """
    if pkg_info.get("format") == "package.json":
        return parse_package_json(
            pkg_info["manifest"], pkg_info.get("repo_root", pkg_info["dir"])
        )
    return parse_manifest(pkg_info["manifest"])


# ===========================================================================
# Package discovery
# ===========================================================================


def discover_packages(lib_dirs, include_unix_ffi=False):
    """
    Scan the micropython-lib tree for packages with manifest.py files.
    Returns dict: package_name -> pkg_info.
    """
    search_dirs = list(lib_dirs)
    if include_unix_ffi:
        search_dirs.append("unix-ffi")

    packages = {}
    for lib_name in search_dirs:
        lib_path = join(LIB_DIR, lib_name)
        if not isdir(lib_path):
            continue
        for manifest_path in find_files(lib_path, "manifest.py"):
            pkg_dir = dirname(manifest_path)
            pkg_name = basename(pkg_dir)
            if pkg_name in packages:
                if lib_name == "unix-ffi":
                    packages[pkg_name] = {
                        "manifest": manifest_path,
                        "dir": pkg_dir,
                        "lib": lib_name,
                        "format": "manifest.py",
                    }
            else:
                packages[pkg_name] = {
                    "manifest": manifest_path,
                    "dir": pkg_dir,
                    "lib": lib_name,
                    "format": "manifest.py",
                }
    return packages


def discover_repo_packages(repo_dir):
    """
    Scan a third-party repo directory for packages.

    Looks for both package.json (mip format) and manifest.py (micropython-lib
    format).  When both exist in the same directory, manifest.py takes
    precedence since it describes the local file layout directly.

    Returns dict: package_name -> pkg_info.
    """
    repo_root = abspath(repo_dir)
    lib_name = basename(repo_root)
    packages = {}

    # Pass 1: package.json files
    for json_path in find_files(repo_root, "package.json"):
        pkg_dir = dirname(json_path)
        pkg_name = basename(pkg_dir)
        packages[pkg_name] = {
            "manifest": json_path,
            "dir": pkg_dir,
            "lib": lib_name,
            "format": "package.json",
            "repo_root": repo_root,
        }

    # Pass 2: manifest.py files (override package.json for same directory)
    for manifest_path in find_files(repo_root, "manifest.py"):
        pkg_dir = dirname(manifest_path)
        pkg_name = basename(pkg_dir)
        packages[pkg_name] = {
            "manifest": manifest_path,
            "dir": pkg_dir,
            "lib": lib_name,
            "format": "manifest.py",
        }

    return packages


# ===========================================================================
# Core logic
# ===========================================================================


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
            print(color("Warning:", _COLOR_WARN), f"Package '{name}' not found, skipping.")
            continue
        if name in resolving:
            continue

        resolving.add(name)
        _, _, deps = parse_package(all_packages[name])
        resolve_dependencies(deps, all_packages, resolved, resolving)
        resolving.discard(name)

        if name not in resolved:
            resolved.append(name)

    return resolved


def collect_files(pkg_name, pkg_info):
    """
    Collect all .py files that need to be copied for a package.
    Returns list of (src_path, target_path) tuples.
    Handles both manifest.py and package.json formats.
    """
    _, file_entries, _ = parse_package(pkg_info)
    pkg_dir = pkg_info["dir"]
    result = []

    for entry_type, name, kwargs in file_entries:

        if entry_type == "url_file":
            src = kwargs["source"]
            target = name
            if exists(src):
                result.append((src, target))
            else:
                print(color("Warning:", _COLOR_WARN), f"File '{src}' not found for '{pkg_name}'.")

        elif entry_type == "module":
            base_path = kwargs.get("base_path", ".")
            if base_path == ".":
                base_path = pkg_dir
            else:
                base_path = join(pkg_dir, base_path)
            src = join(base_path, name)
            if exists(src):
                result.append((src, name))
            else:
                print(color("Warning:", _COLOR_WARN), f"File '{src}' not found in '{pkg_name}'.")

        elif entry_type == "package":
            base_path = kwargs.get("base_path", ".")
            if base_path == ".":
                base_path = pkg_dir
            else:
                base_path = join(pkg_dir, base_path)
            pkg_src_dir = join(base_path, name)
            specified_files = kwargs.get("files")

            if specified_files:
                for rel_file in specified_files:
                    src = join(pkg_src_dir, rel_file)
                    target = join(name, rel_file)
                    if exists(src):
                        result.append((src, target))
                    else:
                        print(
                            color("Warning:", _COLOR_WARN),
                            f"File '{src}' not found in '{pkg_name}'.",
                        )
            else:
                if isdir(pkg_src_dir):
                    for root, dirs, files in walk(pkg_src_dir):
                        dirs.sort()
                        for f in sorted(files):
                            if f.endswith(".py"):
                                src = join(root, f)
                                rel = relpath(src, base_path)
                                result.append((src, rel))
                else:
                    print(
                        color("Warning:", _COLOR_WARN),
                        f"Package directory '{pkg_src_dir}' not found for '{pkg_name}'.",
                    )

    return result


def deploy_packages(package_names, all_packages, output_dir, dry_run=False):
    """Deploy resolved packages to output_dir.  Returns (pkg_count, file_count)."""
    total_files = 0
    total_packages = 0

    for pkg_name in package_names:
        if pkg_name not in all_packages:
            continue

        pkg_info = all_packages[pkg_name]
        files = collect_files(pkg_name, pkg_info)
        metadata, _, _ = parse_package(pkg_info)
        version = metadata.get("version", "")

        if not files:
            print(f"  {color('skip', _COLOR_DIM)} {pkg_name} {color('(no files)', _COLOR_DIM)}")
            continue

        action = color("would install", _COLOR_WARN) if dry_run else color("install", _COLOR_OK)
        ver_str = color(f"@{version}", _COLOR_DIM) if version else ""
        print(f"  {action} {color(pkg_name, _COLOR_BOLD)}{ver_str} [{len(files)} file(s)]")

        for src, target in files:
            dest = join(output_dir, target)
            if dry_run:
                print(f"    {color(target, _COLOR_DIM)} -> {dest}")
            else:
                dest_dir = dirname(dest)
                if dest_dir and not isdir(dest_dir):
                    makedirs(dest_dir, exist_ok=True)
                copy2(src, dest)

        total_files += len(files)
        total_packages += 1

    return total_packages, total_files


def list_packages(all_packages, filter_pattern=None):
    """Print a formatted list of all available packages."""
    names = sorted(all_packages.keys())
    if filter_pattern:
        names = [n for n in names if fnmatch(n, filter_pattern)]

    if not names:
        msg = "No packages found"
        if filter_pattern:
            msg += f" matching '{filter_pattern}'"
        print(msg + ".")
        return

    max_name = max(len(n) for n in names)
    max_lib = max(len(all_packages[n]["lib"]) for n in names)
    max_fmt = 3  # "mpy" or "mip"

    print(
        f"{ljust('Package', max_name)} {ljust('Library', max_lib)} "
        f"{ljust('Fmt', max_fmt)} {ljust('Version', 10)} Dependencies"
    )
    print("-" * (max_name + max_lib + max_fmt + 35))

    for name in names:
        pkg = all_packages[name]
        metadata, _, deps = parse_package(pkg)
        version = metadata.get("version", "")
        dep_str = ", ".join(deps) if deps else ""
        fmt = "mip" if pkg.get("format") == "package.json" else "mpy"
        print(
            f"{color(ljust(name, max_name), _COLOR_BOLD)} "
            f"{ljust(pkg['lib'], max_lib)} "
            f"{ljust(fmt, max_fmt)} "
            f"{ljust(version or '', 10)} "
            f"{color(dep_str, _COLOR_DIM)}"
        )

    print(f"\n{len(names)} package(s) found.")


# ===========================================================================
# Argument parsing (replaces argparse for MicroPython compatibility)
# ===========================================================================

_USAGE = """\
usage: deploy.py [-h] [--output DIR] [--all] [--unix-ffi] [--no-deps]
                 [--dry-run] [--list] [--filter PAT] [--lib-dir DIR]
                 [--repo DIR] [packages ...]

Deploy micropython-lib packages to a local directory for the Unix port.
Compatible with both CPython 3 and MicroPython.

positional arguments:
  packages              Package names to install (use --all for everything).

options:
  -h, --help            Show this help message and exit.
  -o, --output DIR      Destination directory for deployed packages.
  --all                 Deploy all available packages.
  --unix-ffi            Include unix-ffi packages (overrides stdlib for
                        same-named packages).
  --repo DIR            Add a third-party repo to scan for packages.  Supports
                        both manifest.py and package.json formats.  May be
                        specified multiple times.
  --no-deps             Do not install dependencies automatically.
  -n, --dry-run         Show what would be installed without copying files.
  -l, --list            List all available packages and exit.
  --filter PAT          Filter pattern for --list (glob, e.g. 'hash*').
  --lib-dir DIR         Path to micropython-lib root (default: auto-detected).

examples:
  deploy.py -o ~/.micropython/lib logging argparse
  deploy.py -o ~/.micropython/lib --all
  deploy.py -o ~/.micropython/lib --all --unix-ffi
  deploy.py -o lib --repo /path/to/micropython-async primitives threadsafe
  deploy.py --list --repo /path/to/micropython-async
  deploy.py --list --filter 'hash*'
"""


def parse_args(argv=None):
    """Minimal argument parser compatible with both CPython and MicroPython."""
    if argv is None:
        argv = sys.argv[1:]

    class Args:
        packages = []
        output = None
        all = False
        unix_ffi = False
        repos = []
        no_deps = False
        dry_run = False
        list_pkgs = False
        filter = None
        lib_dir = None

    args = Args()
    args.packages = []
    args.repos = []
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
        elif a in ("-o", "--output"):
            args.output = _need_value(a)
        elif a.startswith("--output="):
            args.output = a.split("=", 1)[1]
        elif a == "--all":
            args.all = True
        elif a == "--unix-ffi":
            args.unix_ffi = True
        elif a == "--repo":
            args.repos.append(_need_value(a))
        elif a.startswith("--repo="):
            args.repos.append(a.split("=", 1)[1])
        elif a == "--no-deps":
            args.no_deps = True
        elif a in ("-n", "--dry-run"):
            args.dry_run = True
        elif a in ("-l", "--list"):
            args.list_pkgs = True
        elif a == "--filter":
            args.filter = _need_value(a)
        elif a.startswith("--filter="):
            args.filter = a.split("=", 1)[1]
        elif a == "--lib-dir":
            args.lib_dir = _need_value(a)
        elif a.startswith("--lib-dir="):
            args.lib_dir = a.split("=", 1)[1]
        elif a.startswith("-"):
            print(f"Error: unknown option: {a}", file=sys.stderr)
            print("Use -h for help.", file=sys.stderr)
            sys.exit(2)
        else:
            args.packages.append(a)
        i += 1

    return args


# ===========================================================================
# Entry point
# ===========================================================================


def main():
    args = parse_args()

    global LIB_DIR
    if args.lib_dir:
        LIB_DIR = abspath(args.lib_dir)

    if not isdir(LIB_DIR):
        print(f"Error: micropython-lib not found at '{LIB_DIR}'.", file=sys.stderr)
        sys.exit(1)

    all_packages = discover_packages(DEFAULT_LIB_DIRS, include_unix_ffi=args.unix_ffi)

    for repo_dir in args.repos:
        if not isdir(repo_dir):
            print(f"Error: repo directory not found: '{repo_dir}'.", file=sys.stderr)
            sys.exit(1)
        repo_pkgs = discover_repo_packages(repo_dir)
        all_packages.update(repo_pkgs)

    if args.list_pkgs:
        list_packages(all_packages, args.filter)
        return

    if not args.output:
        print("Error: --output is required when installing packages.", file=sys.stderr)
        print("Use -h for help.", file=sys.stderr)
        sys.exit(2)

    if not args.all and not args.packages:
        print("Error: specify package names or use --all.", file=sys.stderr)
        print("Use -h for help.", file=sys.stderr)
        sys.exit(2)

    output_dir = abspath(args.output)

    if args.all:
        requested = sorted(all_packages.keys())
    else:
        requested = args.packages

    unknown = [p for p in requested if p not in all_packages]
    if unknown:
        print(
            color("Error:", _COLOR_ERR),
            f"Unknown package(s): {', '.join(unknown)}",
            file=sys.stderr,
        )
        print("Use --list to see available packages.", file=sys.stderr)
        sys.exit(1)

    if args.no_deps:
        resolved = requested
    else:
        resolved = resolve_dependencies(requested, all_packages)

    dep_count = len(resolved) - len(requested) if not args.all else 0
    dep_info = f" ({dep_count} deps)" if dep_count > 0 else ""
    dry_tag = color(" (dry run)", _COLOR_WARN) if args.dry_run else ""
    print(f"Deploying {len(resolved)} package(s){dep_info} to {output_dir}{dry_tag}")

    if not args.dry_run:
        makedirs(output_dir, exist_ok=True)

    n_pkgs, n_files = deploy_packages(resolved, all_packages, output_dir, dry_run=args.dry_run)

    verb = "Would deploy" if args.dry_run else "Deployed"
    print(f"\n{verb} {n_pkgs} package(s), {n_files} file(s).")

    if not args.dry_run and n_pkgs > 0:
        print(
            "\nTo use with MicroPython Unix port, ensure MICROPYPATH includes this directory:"
        )
        print(f"  export MICROPYPATH={output_dir}")


if __name__ == "__main__":
    main()
