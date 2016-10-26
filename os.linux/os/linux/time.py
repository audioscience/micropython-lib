
import ustruct
import ffilib
import os


TIMESPEC_FMT = 'll'
TIMESPEC_SIZE = ustruct.calcsize(TIMESPEC_FMT)

CLOCK_MONOTONIC = 1
CLOCK_MONOTONIC_RAW = 4

librt = ffilib.open('librt')
_clock_gettime = librt.func('i', 'clock_gettime', 'ip')
_ts_buf = bytearray(TIMESPEC_SIZE)

def clock_gettime(clk_id):
	e = _clock_gettime(clk_id, _ts_buf)
	os.check_error(e)
	s, ns = ustruct.unpack(TIMESPEC_FMT, _ts_buf)
	return (s*10**9)+ns

def monotime():
	return clock_gettime(CLOCK_MONOTONIC)

def monotime_raw():
	return clock_gettime(CLOCK_MONOTONIC_RAW)
