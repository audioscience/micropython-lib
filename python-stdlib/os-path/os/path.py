import os


sep = "/"


def normcase(s):
    return s


def normpath(s):
    if not s:
        return "."
    slash = "/"
    initial_slashes = s.startswith(slash)
    # POSIX: leading double slash is implementation-defined, keep exactly two.
    if initial_slashes and s.startswith("//") and not s.startswith("///"):
        initial_slashes = 2
    comps = s.split(slash)
    new_comps = []
    for comp in comps:
        if not comp or comp == ".":
            continue
        if comp == "..":
            if new_comps and new_comps[-1] != "..":
                new_comps.pop()
            elif not initial_slashes:
                new_comps.append(comp)
        else:
            new_comps.append(comp)
    s = slash.join(new_comps)
    if initial_slashes:
        s = slash * initial_slashes + s
    return s or "."


def abspath(s):
    if not s.startswith("/"):
        s = os.getcwd() + "/" + s
    return normpath(s)


realpath = os.realpath


def join(a, *p):
    """Combine multiple path components using '/', adding separators as necessary.
    If an absolute path is encountered, all parts before it are ignored.
    If the final component is empty, the result will have a trailing separator."""
    if type(a) is bytes:
        sep = b"/"
    else:
        sep = "/"
    path = a
    for b in p:
        if b.startswith(sep) or not path:
            path = b
        elif path.endswith(sep):
            path += b
        else:
            path += sep + b
    return path


def split(path):
    if path == "":
        return ("", "")
    r = path.rsplit("/", 1)
    if len(r) == 1:
        return ("", path)
    head = r[0]  # .rstrip("/")
    if not head:
        head = "/"
    return (head, r[1])


def dirname(path):
    return split(path)[0]


def basename(path):
    return split(path)[1]


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


# TODO
lexists = exists


def isdir(path):
    try:
        mode = os.stat(path)[0]
        return mode & 0o040000
    except OSError:
        return False


def isfile(path):
    try:
        return bool(os.stat(path)[0] & 0x8000)
    except OSError:
        return False


def expanduser(s):
    if s == "~" or s.startswith("~/"):
        h = os.getenv("HOME")
        return h + s[1:]
    if s[0] == "~":
        # Sorry folks, follow conventions
        return "/home/" + s[1:]
    return s
