import uerrno as errno
import uselect as select
import usocket as _socket
from uasyncio.core import *


DEBUG = 0
log = None

def set_debug(val):
    global DEBUG, log
    DEBUG = val
    if val:
        import logging
        log = logging.getLogger("uasyncio")
        log.setLevel(val)


class EpollEventLoop(EventLoop):

    def __init__(self, runq_len=16, waitq_len=16):
        EventLoop.__init__(self, runq_len, waitq_len)
        self.poller = select.poll()
        self.objmap = {}

    def _unregister_fd(self, fd):
        try:
            self.poller.unregister(fd)
        except OSError as e:
            if e.args[0] == errno.ENOENT:
                if __debug__ and DEBUG:
                    log.debug("_unregister_fd() attepted to remove unregistered FD %s", fd)
            else:
                raise

    def remove_polled_cb(self, cb):
        _id = id(cb)
        for fd, cbs in self.objmap.items():
            cbs.pop(id(cb), None)
            if not cbs:
                self._unregister_fd(fd)

    def add_reader(self, fd, cb, *args):
        if __debug__ and DEBUG:
            log.debug("add_reader%s", (fd, cb, args))
        cbs = self.objmap.setdefault(fd, {})
        self.poller.register(fd, select.POLLIN)
        if args:
            cbs[id(cb)] = (cb, args)
        else:
            cbs[id(cb)] = (cb, None)

    def remove_reader(self, fd, cb):
        if __debug__ and DEBUG:
            log.debug("remove_reader(%s)", (fd, cb))
        cbs = self.objmap.get(fd, {})
        cbs.pop(id(cb), None)
        if not cbs:
            self._unregister_fd(fd)

    def add_writer(self, fd, cb, *args):
        if __debug__ and DEBUG:
            log.debug("add_writer%s", (fd, cb, args))
        cbs = self.objmap.setdefault(fd, {})
        self.poller.register(fd, select.POLLOUT)
        if args:
            cbs[id(cb)] = (cb, args)
        else:
            cbs[id(cb)] = (cb, None)

    def remove_writer(self, fd, cb):
        if __debug__ and DEBUG:
            log.debug("remove_writer(%s)", fd)
        cbs = self.objmap.get(fd, {})
        cbs.pop(id(cb), None)
        if not cbs:
            self._unregister_fd(fd)

    def wait(self, delay):
        if __debug__ and DEBUG:
            log.debug("epoll.wait(%s)", delay)
            for fd, cbs in self.objmap.items():
                for cb, args in cbs.values():
                    log.debug("epoll.registered(%d) %s", fd, (cb, args))

        # We need one-shot behavior (second arg of 1 to .poll())
        res = self.poller.poll(delay, 1)
        #log.debug("epoll result: %s", res)
        for fd, ev in res:
            # Remove the registered callbacks dictionary from its parent
            # so when callbacks are invoked they can add their registrations
            # to a fresh dictionary.
            cbs = self.objmap.pop(fd, {})
            if not cbs:
                log.error("Event %d on fd %r but no callback registered", ev, fd)
                continue
            if __debug__ and DEBUG:
                s = '\n'.join(str(v) for v in cbs.values())
                log.debug("Matching IO callbacks for %r:\n%s", (fd, ev), s)
            while cbs:
                _id, data = cbs.popitem()
                cb, args = data
                if args is None:
                    if __debug__ and DEBUG:
                        log.debug("Scheduling IO coro: %r", (fd, ev, cb))
                    self.call_soon(cb)
                else:
                    if __debug__ and DEBUG:
                        log.debug("Calling IO callback: %r", (fd, ev, cb, args))
                    cb(*args)
            # If no callback registered an event for this fd unregister it
            if not self.objmap.get(fd, None):
                self._unregister_fd(fd)


class StreamReader:

    def __init__(self, s):
        self.s = s

    def read(self, n=-1):
        yield IORead(self.s)
        while True:
            res = self.s.read(n)
            if res is not None:
                break
            log.warning("Empty read")
        if not res:
            yield IOReadDone(self.s)
        return res

    def readline(self):
        if __debug__ and DEBUG:
            log.debug("StreamReader.readline()")
        yield IORead(self.s)
#        if __debug__ and DEBUG:
#            log.debug("StreamReader.readline(): after IORead: %s", s)
        while True:
            res = self.s.readline()
            if res is not None:
                break
            log.warning("Empty read")
        if not res:
            yield IOReadDone(self.s)
        if __debug__ and DEBUG:
            log.debug("StreamReader.readline(): res: %s", res)
        return res

    def aclose(self):
        yield IOReadDone(self.s)
        self.s.close()

    def __repr__(self):
        return "<StreamReader %r>" % self.s


class StreamWriter:

    def __init__(self, s, extra):
        self.s = s
        self.extra = extra

    def awrite(self, buf):
        # This method is called awrite (async write) to not proliferate
        # incompatibility with original asyncio. Unlike original asyncio
        # whose .write() method is both not a coroutine and guaranteed
        # to return immediately (which means it has to buffer all the
        # data), this method is a coroutine.
        sz = len(buf)
        if __debug__ and DEBUG:
            log.debug("StreamWriter.awrite(): spooling %d bytes", sz)
        while True:
            res = self.s.write(buf)
            # If we spooled everything, return immediately
            if res == sz:
                if __debug__ and DEBUG:
                    log.debug("StreamWriter.awrite(): completed spooling %d bytes", res)
                return
            if res is None:
                res = 0
            if __debug__ and DEBUG:
                log.debug("StreamWriter.awrite(): spooled partial %d bytes", res)
            assert res < sz
            buf = buf[res:]
            sz -= res
            yield IOWrite(self.s)
            #assert s2.fileno() == self.s.fileno()
            if __debug__ and DEBUG:
                log.debug("StreamWriter.awrite(): can write more")

    def aclose(self):
        yield IOWriteDone(self.s)
        self.s.close()

    def get_extra_info(self, name, default=None):
        return self.extra.get(name, default)

    def __repr__(self):
        return "<StreamWriter %r>" % self.s


def open_connection(host, port):
    if __debug__ and DEBUG:
        log.debug("open_connection(%s, %s)", host, port)
    s = _socket.socket()
    s.setblocking(False)
    ai = _socket.getaddrinfo(host, port)
    addr = ai[0][4]
    try:
        s.connect(addr)
    except OSError as e:
        if e.args[0] != errno.EINPROGRESS:
            raise
    if __debug__ and DEBUG:
        log.debug("open_connection: After connect")
    yield IOWrite(s)
#    if __debug__ and DEBUG:
#        assert s2.fileno() == s.fileno()
    if __debug__ and DEBUG:
        log.debug("open_connection: After iowait: %s", s)
    return StreamReader(s), StreamWriter(s, {})


def start_server(client_coro, host, port, backlog=10):
    if DEBUG and __debug__:
        log.debug("start_server(%s, %s)", host, port)
    ai = _socket.getaddrinfo(host, port, 0, _socket.SOCK_STREAM)
    ai = ai[0]
    s = _socket.socket(ai[0], ai[1], ai[2])
    try:
        s.setblocking(False)

        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        s.bind(ai[-1])
        s.listen(backlog)
        while True:
            if DEBUG and __debug__:
                log.debug("start_server: Before accept")
            yield IORead(s)
            if DEBUG and __debug__:
                log.debug("start_server: After iowait")
            s2, client_addr = s.accept()
            s2.setblocking(False)
            if DEBUG and __debug__:
                log.debug("start_server: After accept: %s", s2)
            extra = {"peername": client_addr}
            yield client_coro(StreamReader(s2), StreamWriter(s2, extra))
    finally:
        s.close()

import uasyncio.core
uasyncio.core._event_loop_class = EpollEventLoop
