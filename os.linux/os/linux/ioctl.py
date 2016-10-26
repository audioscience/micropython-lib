
import ffilib

libc = ffilib.libc()

TIOCCONS = 0x541D

_IOC_WRITE = 1
_IOC_READ = 2

_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = (_IOC_NRSHIFT+_IOC_NRBITS)
_IOC_SIZESHIFT = (_IOC_TYPESHIFT+_IOC_TYPEBITS)
_IOC_DIRSHIFT = (_IOC_SIZESHIFT+_IOC_SIZEBITS)

def _ioc(dir, type, num, size):
	return ((num << _IOC_NRSHIFT) | (type << _IOC_TYPESHIFT) |
		(size << _IOC_SIZESHIFT) | (dir << _IOC_DIRSHIFT))

ioctl_p = libc.func("i", "ioctl", "iip")
ioctl_l = libc.func("i", "ioctl", "iil")
del libc
