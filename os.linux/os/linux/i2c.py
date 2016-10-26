
import builtins
from os.linux import ioctl

DEV_PATH_TPL = '/dev/i2c-{}'

I2C_SLAVE = 0x0703

def set_slave_addr(dev_fd, addr):
    ioctl.ioctl_l(dev_fd, I2C_SLAVE, addr)

def open(index=0):
    return builtins.open(DEV_PATH_TPL.format(index), 'r+b')
