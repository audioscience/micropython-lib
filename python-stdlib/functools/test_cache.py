from functools import cache


# -- 1. Basic memoisation ---------------------------------------------------

call_count = 0


@cache
def fib(n):
    global call_count
    call_count += 1
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


assert fib(10) == 55
assert call_count == 11, "each unique n should be computed exactly once"

# -- 2. cache_info ----------------------------------------------------------

info = fib.cache_info()
assert info.maxsize is None
assert info.currsize == 11
assert info.hits > 0
assert info.misses == 11

# tuple comparison (matches CPython namedtuple behaviour)
assert info == (info.hits, info.misses, None, 11)

# unpacking
hits, misses, maxsize, currsize = info
assert maxsize is None and currsize == 11

# indexing
assert info[0] == hits and info[3] == currsize

# len
assert len(info) == 4

# repr
r = repr(info)
assert "CacheInfo" in r and "hits=" in r

# -- 3. cache_clear ---------------------------------------------------------

fib.cache_clear()
info = fib.cache_info()
assert info == (0, 0, None, 0), "cache_clear should reset everything"

# recompute after clearing
call_count = 0
assert fib(5) == 5
assert call_count == 6

# -- 4. __wrapped__ ---------------------------------------------------------

assert fib.__wrapped__ is not None
assert callable(fib.__wrapped__)

# -- 5. Keyword arguments ---------------------------------------------------

kw_calls = []


@cache
def greet(name, greeting="hello"):
    kw_calls.append((name, greeting))
    return "%s, %s!" % (greeting, name)


assert greet("world") == "hello, world!"
assert greet("world") == "hello, world!"
assert len(kw_calls) == 1, "second call should be a cache hit"

assert greet("world", greeting="hi") == "hi, world!"
assert len(kw_calls) == 2, "different kwarg value is a separate entry"

assert greet(name="world") == "hello, world!"
assert len(kw_calls) == 3, "positional vs keyword is a separate entry"

# -- 6. Keyword argument order ----------------------------------------------
# CPython dicts are insertion-ordered, so f(a=1, b=2) and f(b=2, a=1)
# produce different cache keys.  MicroPython may not preserve call-site
# kwarg order, so they may share a cache entry.  Both are correct.

order_calls = []


@cache
def multi_kw(a=1, b=2):
    order_calls.append((a, b))
    return a + b


assert multi_kw(a=1, b=2) == 3
assert multi_kw(b=2, a=1) == 3
assert len(order_calls) in (1, 2)

# Verify the behaviour is self-consistent: repeating the same call is always
# a cache hit regardless of the runtime's kwarg ordering.
prev = len(order_calls)
assert multi_kw(a=1, b=2) == 3
assert len(order_calls) == prev, "repeated identical call must be a cache hit"

# -- 7. No-argument function ------------------------------------------------

no_arg_calls = [0]


@cache
def constant():
    no_arg_calls[0] += 1
    return 42


assert constant() == 42
assert constant() == 42
assert constant() == 42
assert no_arg_calls[0] == 1
assert constant.cache_info().hits == 2
assert constant.cache_info().misses == 1
assert constant.cache_info().currsize == 1

# -- 8. None as a valid cached result ---------------------------------------

none_calls = [0]


@cache
def returns_none(x):
    none_calls[0] += 1
    return None


assert returns_none(1) is None
assert returns_none(1) is None
assert none_calls[0] == 1, "None must be cached, not treated as a miss"

# -- 9. Single int/str fast-path key ---------------------------------------

fast_calls = [0]


@cache
def square(x):
    fast_calls[0] += 1
    return x * x


assert square(5) == 25
assert square(5) == 25
assert fast_calls[0] == 1

assert square(7) == 49
assert square(7) == 49
assert fast_calls[0] == 2, "7 is a distinct key from 5"

str_calls = [0]


@cache
def upper(s):
    str_calls[0] += 1
    return s.upper()


assert upper("hello") == "HELLO"
assert upper("hello") == "HELLO"
assert str_calls[0] == 1, "str arg should hit the fast-path cache"

# -- 10. Multiple positional arguments -------------------------------------

multi_calls = [0]


@cache
def add(a, b):
    multi_calls[0] += 1
    return a + b


assert add(1, 2) == 3
assert add(1, 2) == 3
assert add(2, 1) == 3
assert multi_calls[0] == 2, "(1,2) and (2,1) are distinct keys"

# -- 11. Unhashable arguments raise TypeError -------------------------------

@cache
def bad_args(x):
    return x


try:
    bad_args([1, 2, 3])
    assert False, "should have raised TypeError for unhashable arg"
except TypeError:
    pass

# -- 12. Decorated function is still callable as expected -------------------

@cache
def variadic(*args, **kwargs):
    return (args, tuple(sorted(kwargs.items())))


r = variadic(1, 2, x=3)
assert r == ((1, 2), (("x", 3),))
assert variadic(1, 2, x=3) == r
assert variadic.cache_info().hits == 1

# -- 13. Independent caches per decorated function -------------------------

@cache
def fn_a(x):
    return x + 1


@cache
def fn_b(x):
    return x + 2


assert fn_a(1) == 2
assert fn_b(1) == 3
assert fn_a.cache_info().currsize == 1
assert fn_b.cache_info().currsize == 1
fn_a.cache_clear()
assert fn_a.cache_info().currsize == 0
assert fn_b.cache_info().currsize == 1, "clearing fn_a must not affect fn_b"

# -- 14. Large number of entries (unbounded) --------------------------------

@cache
def identity(x):
    return x


for i in range(500):
    assert identity(i) == i

assert identity.cache_info().currsize == 500
assert identity.cache_info().misses == 500

for i in range(500):
    assert identity(i) == i

assert identity.cache_info().hits == 500

print("all cache tests passed")
