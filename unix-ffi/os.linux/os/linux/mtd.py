
import os
from os.linux import ioctl
try:
	import ustruct as struct
except:
	import struct


# struct erase_info_user {
#     __u32 start;
#     __u32 length;
# };

MTD_NORFLASH = 3

def _mtd_ioc(dir, num, size):
    return ioctl._ioc(dir, ord('M'), num, size)

MEM_INFO_STRUCT = 'BIIIIIII'
_MEM_INFO_LEN = struct.calcsize(MEM_INFO_STRUCT)
ERASE_INFO_STRUCT = 'II'
_ERASE_INFO_LEN = struct.calcsize(ERASE_INFO_STRUCT)
MEMGETINFO = _mtd_ioc(ioctl._IOC_READ, 1, _MEM_INFO_LEN)
MEMERASE = _mtd_ioc(ioctl._IOC_WRITE, 2, _ERASE_INFO_LEN)
MEMUNLOCK = _mtd_ioc(ioctl._IOC_WRITE, 6, _ERASE_INFO_LEN)


def _pack_erase_info(start, length):
	return struct.pack(ERASE_INFO_STRUCT, start, length)


def _check_offset(offset, eblock):
	assert offset % eblock == 0, \
		'offset {} is not a multiple of erase block {}'.format(offset, eblock)


def info(mtd_dev):
	mem_info = bytearray(_MEM_INFO_LEN)
	e = ioctl.ioctl_p(mtd_dev.fileno(), MEMGETINFO, mem_info)
	os.check_error(e)
	# do not return padding words
	return struct.unpack(MEM_INFO_STRUCT, mem_info)[:-2]


def unlock(mtd_dev, offset, eblock_size):
	_check_offset(offset, eblock_size)
	erase_info_user = _pack_erase_info(offset, eblock_size)
	e = ioctl.ioctl_p(mtd_dev.fileno(), MEMUNLOCK, erase_info_user)
	os.check_error(e)


def erase(mtd_dev, offset, eblock_size):
	_check_offset(offset, eblock_size)
	erase_info_user = _pack_erase_info(offset, eblock_size)
	e = ioctl.ioctl_p(mtd_dev.fileno(), MEMERASE, erase_info_user)
	os.check_error(e)


def read(mtd_dev, offset, length):
	assert mtd_dev.seek(offset) == offset
	return mtd_dev.read(length)


def write(mtd_dev, offset, data):
	assert mtd_dev.seek(offset) == offset
	return mtd_dev.write(data)
