
import sys
import os
from os import libc

openlog_ = libc.func("v", "openlog", "sii")
setlogmask_ = libc.func("i", "setlogmask", "i")
syslog_ = libc.func("v", "syslog", "is")
isatty_ = libc.func("i", "isatty", "i")

# Syslog priorities
CRITICAL = 2
ERROR    = 3
WARNING  = 4
NOTICE   = 5
INFO     = 6
DEBUG    = 7
NOTSET   = 0

# Facility codes
LOG_USER    = (1<<3) # random user-level messages

# Option flags for openlog
LOG_PID     = 0x01 # log the pid with each message
LOG_CONS    = 0x02 # log on the console if errors in sending
LOG_ODELAY  = 0x04 # delay open until first syslog() (default)
LOG_NDELAY  = 0x08 # don't delay open
LOG_NOWAIT  = 0x10 # don't wait for console forks: DEPRECATED
LOG_PERROR  = 0x20 # log to stderr as well

def _logmask_upto(pri):
	return ((1<<((pri)+1))-1)

class Logger:

    def __init__(self, name):
        self.name = name

    def log(self, level, msg, *args):
        if self.name is not None:
            s = ('%s:'+ msg) % ((self.name,) + args)
        else:
            s = msg % args
        syslog_(level, s)

    def debug(self, msg, *args):
        self.log(DEBUG, msg, *args)

    def info(self, msg, *args):
        self.log(INFO, msg, *args)

    def warning(self, msg, *args):
        self.log(WARNING, msg, *args)

    def error(self, msg, *args):
        self.log(ERROR, msg, *args)

    def critical(self, msg, *args):
        self.log(CRITICAL, msg, *args)


_level = ERROR
_loggers = {}

r = isatty_(sys.stdout.fileno())
os.check_error(r)

flags = LOG_CONS | LOG_PID
# if we are outputting to a tty log also to stderr
if r > 0:
    flags |= LOG_PERROR
ident = 'python'
if len(sys.argv):
    ident = sys.argv[0]
openlog_(ident, flags, LOG_USER)

r = setlogmask_(_logmask_upto(_level))
os.check_error(r)

def getLogger(name):
    if name in _loggers:
        return _loggers[name]
    l = Logger(name)
    _loggers[name] = l
    return l

def info(msg, *args):
    getLogger(None).info(msg, *args)

def debug(msg, *args):
    getLogger(None).debug(msg, *args)

def basicConfig(level=INFO, filename=None, format=None):
    global _level
    _level = level
    if filename is not None:
        print("logging.basicConfig: filename arg is not supported")
    if format is not None:
        print("logging.basicConfig: format arg is not supported")
