
import ffilib
import os


libc = ffilib.libc()
sleep = libc.func('I', 'sleep', 'I')
_mount = libc.func('i', 'mount', 'sssLs')
_umount = libc.func('i', 'umount', 's')
_setenv = libc.func('i', 'setenv', 'ssi')
_reboot_syscall = libc.func('l', 'syscall', 'liiis')


LINUX_REBOOT_MAGIC1         = 0xfee1dead
LINUX_REBOOT_MAGIC2         = 672274793
LINUX_REBOOT_CMD_RESTART	= 0x01234567
LINUX_REBOOT_CMD_HALT		= 0xCDEF0123
LINUX_REBOOT_CMD_CAD_ON		= 0x89ABCDEF
LINUX_REBOOT_CMD_CAD_OFF	= 0x00000000
LINUX_REBOOT_CMD_POWER_OFF	= 0x4321FEDC
LINUX_REBOOT_CMD_RESTART2	= 0xA1B2C3D4
LINUX_REBOOT_CMD_SW_SUSPEND	= 0xD000FCE2
LINUX_REBOOT_CMD_KEXEC		= 0x45584543


def reboot(cmd, arg_str):
	SYS_reboot = 142
	e = _reboot_syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2, cmd, arg_str)
	os.check_error(e)


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
