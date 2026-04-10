"""Unit tests for json module memory leak fix.

The primary bug: scanner.py py_make_scanner() returned _scan_once (the inner
function) instead of scan_once (the wrapper that clears the memo dict).
This caused the decoder's memo dict to grow unboundedly across calls,
leaking every unique JSON object key string ever parsed.
"""

import sys
import gc
import json
from json.decoder import JSONDecoder
from json import scanner


def test_memo_cleared_after_loads():
    """Memo dict must be empty after json.loads() completes."""
    json.loads('{"a": 1, "b": 2, "c": 3}')
    assert len(json._default_decoder.memo) == 0, \
        "memo not cleared after loads()"


def test_memo_cleared_after_repeated_loads():
    """Repeated loads() must not accumulate memo entries."""
    for i in range(100):
        json.loads('{"key_%d": %d}' % (i, i))
    assert len(json._default_decoder.memo) == 0, \
        "memo accumulated entries across loads() calls"


def test_memo_cleared_after_nested_objects():
    """Memo must be cleared even with deeply nested objects."""
    json.loads('{"outer": {"middle": {"inner": "value"}}}')
    assert len(json._default_decoder.memo) == 0, \
        "memo not cleared after nested object parse"


def test_memo_cleared_after_array_of_objects():
    """Memo must be cleared after parsing arrays of objects."""
    json.loads('[{"k1": 1}, {"k2": 2}, {"k3": 3}]')
    assert len(json._default_decoder.memo) == 0, \
        "memo not cleared after array-of-objects parse"


def test_memo_cleared_with_custom_decoder():
    """A fresh JSONDecoder instance must also clear its memo."""
    dec = JSONDecoder()
    dec.decode('{"x": 1, "y": 2}')
    assert len(dec.memo) == 0, \
        "custom decoder memo not cleared after decode()"


def test_no_leak_under_repeated_parsing():
    """Memory must not grow when parsing the same structure repeatedly.

    Uses gc to measure retained object count.
    """
    gc.collect()
    baseline = gc.mem_alloc() if hasattr(gc, 'mem_alloc') else None

    for _ in range(1000):
        json.loads('{"sensor": 42, "status": "ok", "values": [1,2,3]}')

    gc.collect()
    after = gc.mem_alloc() if hasattr(gc, 'mem_alloc') else None

    assert len(json._default_decoder.memo) == 0, \
        "memo leaked after 1000 iterations"

    if baseline is not None and after is not None:
        growth = after - baseline
        assert growth < 4096, \
            "memory grew by %d bytes over 1000 iterations" % growth


def test_scan_once_is_wrapper():
    """make_scanner must return the wrapper that clears memo, not _scan_once."""
    dec = JSONDecoder()
    # The scan_once stored on the decoder should be the wrapper (scan_once),
    # which has a finally clause that calls memo.clear().
    # Verify indirectly: after calling scan_once, memo must be empty.
    dec.scan_once('{"test": 1}', 0)
    assert len(dec.memo) == 0, \
        "scan_once did not clear memo — wrong function returned by make_scanner"


def test_basic_parsing_still_works():
    """Verify the fix doesn't break normal JSON parsing."""
    assert json.loads('null') is None
    assert json.loads('true') is True
    assert json.loads('false') is False
    assert json.loads('42') == 42
    assert json.loads('3.14') == 3.14
    assert json.loads('"hello"') == "hello"
    assert json.loads('[1, 2, 3]') == [1, 2, 3]
    assert json.loads('{"a": 1}') == {"a": 1}


def test_nested_parsing_still_works():
    """Verify complex nested structures parse correctly."""
    data = json.loads(
        '{"users": [{"name": "alice", "age": 30}, '
        '{"name": "bob", "age": 25}], "count": 2}'
    )
    assert data["count"] == 2
    assert len(data["users"]) == 2
    assert data["users"][0]["name"] == "alice"
    assert data["users"][1]["age"] == 25


def test_encoding_still_works():
    """Verify encoding is unaffected by the fix."""
    assert json.dumps({"a": 1}) == '{"a": 1}'
    assert json.dumps([1, 2, 3]) == '[1, 2, 3]'
    assert json.dumps(None) == 'null'
    assert json.dumps(True) == 'true'


def test_roundtrip():
    """Verify encode/decode roundtrip works correctly."""
    original = {"key": [1, 2.5, "three", None, True, False, {"nested": "obj"}]}
    encoded = json.dumps(original)
    decoded = json.loads(encoded)
    assert decoded == original, \
        "roundtrip failed: %r != %r" % (decoded, original)


def test_key_interning_within_single_parse():
    """Within a single parse, duplicate keys should still be interned via memo.

    The memo is only cleared AFTER the top-level scan_once returns, so
    within a single document, key deduplication still works.
    """
    data = json.loads('[{"id": 1}, {"id": 2}, {"id": 3}]')
    keys = [list(obj.keys())[0] for obj in data]
    assert all(k == "id" for k in keys)
    # After the parse, memo should be clean
    assert len(json._default_decoder.memo) == 0


# --- run all tests ---

def run_tests():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    passed = 0
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            test()
            print("  PASS:", name)
            passed += 1
        except Exception as e:
            print("  FAIL:", name, "-", e)
            failed += 1
    print("\n%d passed, %d failed" % (passed, failed))
    return failed


if __name__ == '__main__':
    print("Running json memory leak tests...")
    failures = run_tests()
    sys.exit(1 if failures else 0)
