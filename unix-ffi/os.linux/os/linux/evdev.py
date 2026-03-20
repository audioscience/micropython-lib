
import builtins
import os
from os.linux import ioctl

DEV_PATH_TPL = '/dev/input/event{}'

KEY_SELECT = 0x161

def EVIOCGKEY(len):
    return ioctl._ioc(ioctl._IOC_READ, ord('E'), 0x18, len)

def get_global_keystate(dev, byte_array):
    r = ioctl.ioctl_p(dev.fileno(), EVIOCGKEY(len(byte_array)), byte_array)
    os.check_error(r)

def open(index=0):
    return builtins.open(DEV_PATH_TPL.format(index), 'r+b')
