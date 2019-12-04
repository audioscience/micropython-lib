try:
    import utime as time
except ImportError:
    import time
import uheapq as heapq
import logging

class CancelledError(Exception):
    pass

log = logging.getLogger("asyncio")

type_gen = type((lambda: (yield))())

class EventLoop:

    def __init__(self):
        self.q = []
        self.cnt = 0

    def time(self):
        return time.time()

    def create_task(self, coro):
        # CPython 3.4.2
        self.call_at(0, coro)
        # CPython asyncio incompatibility: we don't return Task object

    def call_soon(self, callback, *args):
        self.call_at(self.time(), callback, *args)

    def call_later(self, delay, callback, *args):
        self.call_at(self.time() + delay, callback, *args)

    def call_at(self, time, callback, *args, exc=None):
        # Including self.cnt is a workaround per heapq docs
        if __debug__:
            log.debug("Scheduling %s", (time, self.cnt, callback, args, exc))
        heapq.heappush(self.q, (time, self.cnt, callback, args, exc, False))
#        print(self.q)
        self.cnt += 1

    def wait(self, delay):
        # Default wait implementation, to be overriden in subclasses
        # with IO scheduling
        if __debug__:
            log.debug("Sleeping for: %s", delay)
        time.sleep(delay)

    def cancel(self, callback, exc = CancelledError):
        _id = id(callback)
        for idx, item in enumerate(self.q):
            t, cnt, cb, args, _exc = item
            if id(cb) != _id:
                continue
            if __debug__:
                log.debug("Setting discard flag on: %s at index %d", (t, cnt, cb, args, _exc), idx)
            self.q[idx] = t, cnt, cb, args, _exc, True
            self.call_at(0, cb, *args, exc=exc)
        self.remove_polled_cb(callback)

    def run_forever(self):
        while True:
            if self.q:
                tnow = self.time()
                if __debug__:
                    log.debug('*'*20+' sched step start at %s, num tasks in queue %d', tnow, len(self.q))
                t, cnt, cb, args, exc, discard = heapq.heappop(self.q)
                delay = t - tnow
                if __debug__:
                    log.debug("Next coroutine to run in %s: %s", delay, (t, cnt, cb, args, exc))
                if discard:
                    if __debug__:
                        log.debug("Discarding: %s", (t, cnt, cb, args, exc, discard))
                    continue
#                __main__.mem_info()
                if delay > 0 and not exc:
                    self.call_at(t, cb, *args)
                    self.wait(delay)
                    continue
            else:
                self.wait(-1)
                # Assuming IO completion scheduled some tasks
                continue
            # cancelled callbacks aren't called and nor rescheduled
            if callable(cb):
                if not exc:
                    cb(*args)
            else:
                delay = 0
                try:
                    if __debug__:
                        log.debug("Coroutine %s send args: %s, %s", cb, args, exc)
                    if exc:
                        try:
                            ret = cb.throw(exc)
                        except exc:
                            # ret == None reschedules a canceled task, next round it should raise StopIteration
                            ret = None
                    elif args == ():
                        ret = next(cb)
                    else:
                        ret = cb.send(*args)
                    if __debug__:
                        log.debug("Coroutine %s yield result: %s", cb, ret)
                    if isinstance(ret, SysCall1):
                        arg = ret.arg
                        if isinstance(ret, Sleep):
                            delay = arg
                        elif isinstance(ret, IORead):
#                            self.add_reader(ret.obj.fileno(), lambda self, c, f: self.call_soon(c, f), self, cb, ret.obj)
#                            self.add_reader(ret.obj.fileno(), lambda c, f: self.call_soon(c, f), cb, ret.obj)
#                            self.add_reader(arg.fileno(), lambda cb: self.call_soon(cb), cb)
                            self.add_reader(arg.fileno(), cb)
                            continue
                        elif isinstance(ret, IOWrite):
#                            self.add_writer(arg.fileno(), lambda cb: self.call_soon(cb), cb)
                            self.add_writer(arg.fileno(), cb)
                            continue
                        elif isinstance(ret, IOReadDone):
                            self.remove_reader(arg.fileno(), cb)
                        elif isinstance(ret, IOWriteDone):
                            self.remove_writer(arg.fileno(), cb)
                        elif isinstance(ret, StopLoop):
                            return arg
                    elif isinstance(ret, type_gen):
                        self.call_soon(ret)
                    elif ret is None:
                        # Just reschedule
                        pass
                    else:
                        assert False, "Unsupported coroutine yield value: %r (of type %r)" % (ret, type(ret))
                except StopIteration as e:
                    if __debug__:
                        log.debug("Coroutine finished: %s", cb)
                    continue
                self.call_later(delay, cb, *args)

    def run_until_complete(self, coro):
        def _run_and_stop():
            yield from coro
            yield StopLoop(0)
        self.call_soon(_run_and_stop())
        self.run_forever()

    def close(self):
        pass


class SysCall:

    def __init__(self, *args):
        self.args = args

    def handle(self):
        raise NotImplementedError

# Optimized syscall with 1 arg
class SysCall1(SysCall):

    def __init__(self, arg):
        self.arg = arg

class Sleep(SysCall1):
    pass

class StopLoop(SysCall1):
    pass

class IORead(SysCall1):
    pass

class IOWrite(SysCall1):
    pass

class IOReadDone(SysCall1):
    pass

class IOWriteDone(SysCall1):
    pass


_event_loop = None
_event_loop_class = EventLoop
def get_event_loop():
    global _event_loop
    if _event_loop is None:
        _event_loop = _event_loop_class()
    return _event_loop

def sleep(secs):
    yield Sleep(secs)

def coroutine(f):
    return f

#
# The functions below are deprecated in uasyncio, and provided only
# for compatibility with CPython asyncio
#

def ensure_future(coro, loop=_event_loop):
    _event_loop.call_soon(coro)
    # CPython asyncio incompatibility: we don't return Task object
    return coro


# CPython asyncio incompatibility: Task is a function, not a class (for efficiency)
def Task(coro, loop=_event_loop):
    # Same as async()
    _event_loop.call_soon(coro)
