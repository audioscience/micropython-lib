
import os
import ffi
import uasyncio
import functools
import logging
from collections.deque import deque


class TimeoutError(Exception):
    pass


class ATimeout:
    def __init__(self, task, timeout_seconds, exc = TimeoutError):
        self._task = task
        self._timeout_task = self._fire(exc)
        self._timeout = timeout_seconds

    def __enter__(self):
        return self.start()

    def __exit__(self, type, value, tb):
        self.cancel()

    def start(self):
        uasyncio.get_event_loop().call_later(self._timeout, self._timeout_task)
        return self

    def cancel(self):
        self._timeout_task.close()

    async def run(self):
        self.start()
        res = await self._task
        self.cancel()
        return res

    async def _fire(self, exc):
        self._task.throw(exc)


class AFuture:
    def __init__(self, coro = None):
        self._coro = coro
        self.cancelled = False
        self.closed = False
        self._result = deque()
        _in, _out = os.pipe()
        self._in, self._out = open(_in, 'rb'), open(_out, 'wb')
        self.done = self.result
        self.waiting = 0
        self.pending_bytes = 0

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        self.close()

    async def task(self):
        try:
            res = await self._coro
            self.set_result(res)
        except Exception as e:
            self.set_result(e)
            raise

    def cancel(self):
        if self.closed:
            if self.waiting:
                raise RuntimeError('Canceling a closed future %s with %d waiting tasks', self, self.waiting)
            return
        if not self.cancelled:
            self.cancelled = True
            self._wake_waiting()
            if self._coro:
                uasyncio.get_event_loop().cancel(self._coro)

    def close(self):
        if self.waiting:
            logging.warning('Closing %s with %d waiting tasks', self, self.waiting)
        if not self.closed:
            self.cancel()
            self.reset()
            self._in.close()
            self._out.close()
            self.closed = True

    def has_result(self):
        return len(self._result) > 0

    def reset(self):
        if self.closed:
            raise ValueError('reset() on a closed future')
        self._in.read(self.pending_bytes)
        self.pending_bytes = 0
        self.cancelled = False
        # No clear() in upy's ucollections implementation!
        while len(self._result):
            self._result.pop()

    def _wake_waiting(self):
        self.pending_bytes += 1
        self._out.write('-')


    def set_result(self, result):
        #if self.cancelled or self.closed:
        #    raise RuntimeError('set_result() with cancelled %s and closed %s'.format(self.cancelled, self.closed))
        self._result.append(result)
        # Signal result is available
        self._wake_waiting()

    def result_nowait(self, consume=True, flush=False):
        # No subscripting in upy's ucollections implementation, so need to pop
        # then append :(
        res = self._result.popleft()
        if flush:
            self.reset()
        elif not consume:
            self._result.appendleft(res)
        return res

    async def result(self, consume=True, flush=False):
        self.waiting += 1
        while not len(self._result) and not self.cancelled:
            yield uasyncio.IORead(self._in)
            if consume:
                self._in.read(1)
                self.pending_bytes -= 1
        self.waiting -= 1
        if self.cancelled:
            raise uasyncio.CancelledError
        return self.result_nowait(consume=consume, flush=flush)


class AsyncCallback(AFuture):
    def __init__(self, _callable):
        super().__init__()
        self.cb = functools.partial(_callable, self)
