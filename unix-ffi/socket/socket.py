from usocket import *
import usocket as _socket


_GLOBAL_DEFAULT_TIMEOUT = 30
IPPROTO_IP = 0
INADDR_ANY = 0

error = OSError


def _resolve_addr(addr):
    if isinstance(addr, (bytes, bytearray)):
        return addr
    family = _socket.AF_INET
    if len(addr) != 2:
        family = _socket.AF_INET6
    if addr[0] == "":
        a = "0.0.0.0" if family == _socket.AF_INET else "::"
    else:
        a = addr[0]
    a = getaddrinfo(a, addr[1], family)
    return a[0][4]


def inet_aton(addr):
    return inet_pton(AF_INET, addr)


# Do NOT subclass _socket.socket in Python: MicroPython's stream protocol
# inheritance is broken for Python subclasses of C types -- the C-level
# MP_STREAM_GET_FILENO ioctl reads from the mp_obj_instance_t layout
# (members map) instead of the mp_obj_socket_t layout, returning a garbage
# fd value.  This causes select.poll / asyncio to poll the wrong fd.
# Use the C type directly and provide helpers as standalone functions.
socket = _socket.socket


def _wrap_accept(sock):
    """Accept a connection, returning (new_socket, (ip_str, port))."""
    s, addr = sock.accept()
    addr = _socket.sockaddr(addr)
    return (s, (_socket.inet_ntop(addr[0], addr[1]), addr[2]))


def create_connection(addr, timeout=None, source_address=None):
    s = socket()
    ais = getaddrinfo(addr[0], addr[1])
    for ai in ais:
        try:
            s.connect(ai[4])
            return s
        except:
            pass
