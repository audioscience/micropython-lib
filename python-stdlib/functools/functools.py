def partial(func, *args, **kwargs):
    def _partial(*more_args, **more_kwargs):
        kw = kwargs.copy()
        kw.update(more_kwargs)
        return func(*(args + more_args), **kw)

    return _partial


def update_wrapper(wrapper, wrapped, assigned=None, updated=None):
    # Dummy impl
    return wrapper


def wraps(wrapped, assigned=None, updated=None):
    # Dummy impl
    return lambda x: x


def reduce(function, iterable, initializer=None):
    it = iter(iterable)
    if initializer is None:
        value = next(it)
    else:
        value = initializer
    for element in it:
        value = function(value, element)
    return value


# ---------------------------------------------------------------------------
# cache — unbounded memoisation decorator (equivalent to CPython's
# functools.cache / lru_cache(maxsize=None))
# ---------------------------------------------------------------------------

_kwd_mark = (object(),)
_fasttypes = {int, str}


def _make_key(args, kwds):
    """Build a flat, hashable cache key from positional and keyword arguments.

    When there is a single positional argument whose type is int or str and
    no keyword arguments, the argument itself is returned (avoiding the
    overhead of a one-element tuple lookup).

    Keyword argument order matters: f(x=1, y=2) and f(y=2, x=1) produce
    different keys, matching CPython's behaviour.
    """
    key = args
    if kwds:
        key += _kwd_mark
        for item in kwds.items():
            key += item
    elif len(key) == 1 and type(key[0]) in _fasttypes:
        return key[0]
    return key


class _CacheInfo:
    """Lightweight equivalent of collections.namedtuple('CacheInfo', ...)."""

    __slots__ = ("hits", "misses", "maxsize", "currsize")

    def __init__(self, hits, misses, maxsize, currsize):
        self.hits = hits
        self.misses = misses
        self.maxsize = maxsize
        self.currsize = currsize

    def __repr__(self):
        return "CacheInfo(hits=%d, misses=%d, maxsize=%s, currsize=%d)" % (
            self.hits,
            self.misses,
            self.maxsize,
            self.currsize,
        )

    def __eq__(self, other):
        if isinstance(other, _CacheInfo):
            return (
                self.hits == other.hits
                and self.misses == other.misses
                and self.maxsize == other.maxsize
                and self.currsize == other.currsize
            )
        if isinstance(other, tuple) and len(other) == 4:
            return (
                self.hits == other[0]
                and self.misses == other[1]
                and self.maxsize == other[2]
                and self.currsize == other[3]
            )
        return NotImplemented

    def __iter__(self):
        return iter((self.hits, self.misses, self.maxsize, self.currsize))

    def __getitem__(self, i):
        return (self.hits, self.misses, self.maxsize, self.currsize)[i]

    def __len__(self):
        return 4


def cache(user_function):
    """Simple lightweight unbounded cache.  Sometimes called 'memoize'.

    Equivalent to CPython's functools.cache (lru_cache(maxsize=None)).
    Returns a callable with cache_info(), cache_clear(), and __wrapped__.

    Uses a callable class instead of a closure with function attributes
    because MicroPython closures do not support attribute assignment.
    """
    sentinel = object()
    _cache = {}
    _stats = [0, 0]  # [hits, misses]

    class _Cached:
        __wrapped__ = user_function

        def __call__(self, *args, **kwds):
            key = _make_key(args, kwds)
            result = _cache.get(key, sentinel)
            if result is not sentinel:
                _stats[0] += 1
                return result
            _stats[1] += 1
            result = user_function(*args, **kwds)
            _cache[key] = result
            return result

        def cache_info(self):
            """Report cache statistics."""
            return _CacheInfo(_stats[0], _stats[1], None, len(_cache))

        def cache_clear(self):
            """Clear the cache and cache statistics."""
            _cache.clear()
            _stats[0] = _stats[1] = 0

    return _Cached()
