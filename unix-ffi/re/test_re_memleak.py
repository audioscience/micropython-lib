"""Tests for FFI memory leak fixes in the re module.

Verifies that:
- pcre2_match_data is freed after every search/match (C heap leak per operation)
- pcre2_code is freed via cache eviction (C heap leak per compile)
- Pattern cache prevents recompilation and bounds memory usage
"""
import gc
import re

PASS = 0
FAIL = 0


def _get_rss_pages():
    """Return current RSS in pages from /proc/self/statm."""
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1])


def _run(name, func):
    global PASS, FAIL
    try:
        func()
        PASS += 1
        print("  PASS:", name)
    except Exception as e:
        FAIL += 1
        print("  FAIL:", name, "-", e)


# ---------------------------------------------------------------------------
# Functional correctness
# ---------------------------------------------------------------------------

def test_search_still_works():
    m = re.search(r"a+", "caaab")
    assert m.group(0) == "aaa"


def test_match_still_works():
    m = re.match(r"a+", "aaab")
    assert m.group(0) == "aaa"
    assert re.match(r"a+", "bbb") is None


def test_sub_still_works():
    assert re.sub("a", "z", "caaab") == "czzzb"


def test_findall_still_works():
    assert re.findall(r"\w+ly", "carefully and quickly") == ["carefully", "quickly"]


def test_split_still_works():
    assert re.split(r"\W+", "one, two, three") == ["one", "two", "three"]


def test_compiled_pattern_reuse():
    """Compiled patterns work correctly across many calls."""
    pat = re.compile(r"(\d+)")
    for i in range(100):
        m = pat.match(str(i))
        assert m is not None
        assert m.group(1) == str(i)


def test_no_match_returns_none():
    pat = re.compile(r"xyz")
    for _ in range(1000):
        assert pat.search("abc") is None


def test_groups_and_captures():
    m = re.match(r"(\d+)\.(\d+)", "24.1632")
    assert m.groups() == ("24", "1632")
    assert m.group(2, 1) == ("1632", "24")


def test_sub_with_callable():
    assert re.sub("a", lambda m: m.group(0) * 2, "caaab") == "caaaaaab"


# ---------------------------------------------------------------------------
# Pattern cache tests
# ---------------------------------------------------------------------------

def test_cache_reuses_pattern():
    """Same pattern string should return cached compiled pattern."""
    re.purge()
    re.search(r"test_cache_1", "x")
    assert (r"test_cache_1", 0) in re._cache
    re.search(r"test_cache_1", "y")
    assert len([k for k in re._cache if k[0] == r"test_cache_1"]) == 1


def test_cache_eviction():
    """Exceeding _CACHE_MAX evicts oldest entries."""
    re.purge()
    for i in range(re._CACHE_MAX + 10):
        re.search("evict_%d" % i, "evict_%d" % i)
    assert len(re._cache) == re._CACHE_MAX


def test_purge_clears_cache():
    re.search("purge_test", "purge_test")
    assert len(re._cache) > 0
    re.purge()
    assert len(re._cache) == 0


# ---------------------------------------------------------------------------
# C heap memory leak tests (use gc.threshold to keep Python heap clean)
# ---------------------------------------------------------------------------

def test_match_data_no_leak():
    """Repeated search/match must not grow the C heap (pcre2_match_data freed).

    Before the fix, each search() leaked ~48+ bytes of pcre2_match_data.
    Over 50k iterations that's ~2+ MB of RSS growth.
    """
    old_thresh = gc.threshold()
    gc.threshold(4096)
    pat = re.compile(r"(\w+)\s+(\w+)")
    gc.collect()
    rss_before = _get_rss_pages()
    for _ in range(50000):
        pat.search("hello world foo bar")
    gc.collect()
    rss_after = _get_rss_pages()
    gc.threshold(old_thresh)
    growth = rss_after - rss_before
    assert growth < 50, "RSS grew by %d pages; pcre2_match_data likely leaking" % growth


def test_match_data_no_leak_on_no_match():
    """Non-matching searches must also free match_data."""
    old_thresh = gc.threshold()
    gc.threshold(4096)
    pat = re.compile(r"xyz123")
    gc.collect()
    rss_before = _get_rss_pages()
    for _ in range(50000):
        pat.search("nothing here")
    gc.collect()
    rss_after = _get_rss_pages()
    gc.threshold(old_thresh)
    growth = rss_after - rss_before
    assert growth < 50, "RSS grew by %d pages on no-match path" % growth


def test_cached_patterns_no_leak():
    """Convenience functions use the cache, so pcre2_code doesn't leak."""
    re.purge()
    old_thresh = gc.threshold()
    gc.threshold(4096)
    gc.collect()
    rss_before = _get_rss_pages()
    for _ in range(50000):
        re.search(r"cached_\d+", "cached_123")
    gc.collect()
    rss_after = _get_rss_pages()
    gc.threshold(old_thresh)
    re.purge()
    growth = rss_after - rss_before
    assert growth < 50, "RSS grew by %d pages; pcre2_code likely leaking" % growth


def test_eviction_frees_code():
    """Evicted patterns must have their pcre2_code freed."""
    re.purge()
    old_thresh = gc.threshold()
    gc.threshold(4096)
    gc.collect()
    rss_before = _get_rss_pages()
    for i in range(500):
        re.search("evict_free_%d" % i, "evict_free_%d" % i)
    gc.collect()
    rss_after = _get_rss_pages()
    gc.threshold(old_thresh)
    re.purge()
    growth = rss_after - rss_before
    assert growth < 50, "RSS grew by %d pages during cache eviction" % growth


def test_sub_repeated_no_leak():
    """sub() calls search() in a loop; match_data must be freed each time."""
    re.purge()
    old_thresh = gc.threshold()
    gc.threshold(4096)
    gc.collect()
    rss_before = _get_rss_pages()
    for _ in range(5000):
        re.sub(r"\s+", "-", "one two three four five")
    gc.collect()
    rss_after = _get_rss_pages()
    gc.threshold(old_thresh)
    re.purge()
    growth = rss_after - rss_before
    assert growth < 50, "RSS grew by %d pages during repeated sub()" % growth


def test_json_like_workload_no_leak():
    """Simulate JSON parsing regex workload (STRINGCHUNK, WHITESPACE, NUMBER)."""
    STRINGCHUNK = re.compile(r'(.*?)(["\\\x00-\x1f])', re.VERBOSE | re.MULTILINE | re.DOTALL)
    WHITESPACE = re.compile(r"[ \t\n\r]*", re.VERBOSE | re.MULTILINE | re.DOTALL)
    NUMBER_RE = re.compile(
        r"(-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)?",
        re.VERBOSE | re.MULTILINE | re.DOTALL,
    )
    test_strings = ['"hello"', '"world"', '  \n\t  ', "12345", "-3.14e10"]

    old_thresh = gc.threshold()
    gc.threshold(4096)
    gc.collect()
    rss_before = _get_rss_pages()
    for _ in range(10000):
        for s in test_strings:
            STRINGCHUNK.match(s)
            WHITESPACE.match(s)
            NUMBER_RE.match(s)
    gc.collect()
    rss_after = _get_rss_pages()
    gc.threshold(old_thresh)
    growth = rss_after - rss_before
    assert growth < 50, (
        "RSS grew by %d pages during JSON-like regex workload" % growth
    )


def test_explicit_free():
    """_free() releases pcre2_code and invalidates the pattern."""
    p = re.compile(r"explicit_free_test")
    assert p.obj is not None
    p._free()
    assert p.obj is None


if __name__ == "__main__":
    print("Running re FFI memory leak tests...")
    _run("test_search_still_works", test_search_still_works)
    _run("test_match_still_works", test_match_still_works)
    _run("test_sub_still_works", test_sub_still_works)
    _run("test_findall_still_works", test_findall_still_works)
    _run("test_split_still_works", test_split_still_works)
    _run("test_compiled_pattern_reuse", test_compiled_pattern_reuse)
    _run("test_no_match_returns_none", test_no_match_returns_none)
    _run("test_groups_and_captures", test_groups_and_captures)
    _run("test_sub_with_callable", test_sub_with_callable)
    _run("test_cache_reuses_pattern", test_cache_reuses_pattern)
    _run("test_cache_eviction", test_cache_eviction)
    _run("test_purge_clears_cache", test_purge_clears_cache)
    _run("test_match_data_no_leak", test_match_data_no_leak)
    _run("test_match_data_no_leak_on_no_match", test_match_data_no_leak_on_no_match)
    _run("test_cached_patterns_no_leak", test_cached_patterns_no_leak)
    _run("test_eviction_frees_code", test_eviction_frees_code)
    _run("test_sub_repeated_no_leak", test_sub_repeated_no_leak)
    _run("test_json_like_workload_no_leak", test_json_like_workload_no_leak)
    _run("test_explicit_free", test_explicit_free)
    print()
    print("%d passed, %d failed" % (PASS, FAIL))
    if FAIL:
        raise SystemExit(1)
