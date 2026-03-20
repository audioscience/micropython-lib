
import builtins
from os.linux import ioctl

DEV_PATH_TPL = '/dev/spidev{}.{}'

def _spidev_ioc(dir, num, size):
    return ioctl._ioc(dir, ord('k'), num, size)

SPI_IOC_RD_BITS_PER_WORD = _spidev_ioc(ioctl._IOC_READ, 3, 1)
SPI_IOC_WR_BITS_PER_WORD = _spidev_ioc(ioctl._IOC_WRITE, 3, 1)

SPI_IOC_RD_MAX_SPEED_HZ = _spidev_ioc(ioctl._IOC_READ, 4, 4)
SPI_IOC_WR_MAX_SPEED_HZ = _spidev_ioc(ioctl._IOC_WRITE, 4, 4)

def open(bus_idx=0, cs_idx=0):
    return builtins.open(DEV_PATH_TPL.format(bus_idx, cs_idx), 'r+b')
