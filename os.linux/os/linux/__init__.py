
import ffilib
import os


libc = ffilib.libc()
sleep = libc.func('I', 'sleep', 'I')
_mount = libc.func('i', 'mount', 'sssLs')
_umount = libc.func('i', 'umount', 's')
_setenv = libc.func('i', 'setenv', 'ssi')


def mount(source, target, fstype, flags = 0, opts = None):
	e = _mount(source, target, fstype, flags, opts)
	os.check_error(e)


def umount(target):
	e = _umount(target)
	os.check_error(e)


def execv(path, args = []):
	assert args, '`args` argument cannot be empty'
	_args = [path] + args + [None]
	_execl = libc.func('i', 'execl', 's'*len(_args))
	e = _execl(*_args)
	os.check_error(e)


def execvp(executable, args = []):
	assert args, '`args` argument cannot be empty'
	_args = [executable] + args + [None]
	_execlp = libc.func('i', 'execlp', 's'*len(_args))
	e = _execlp(*_args)
	os.check_error(e)


def setenv(name, value, overwrite = True):
	e = _setenv(name, value, 1 if overwrite else 0)
	os.check_error(e)
