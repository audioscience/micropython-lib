
import ustruct
import ffilib
import os


TIMESPEC_FMT = 'll'
TIMESPEC_SIZE = ustruct.calcsize(TIMESPEC_FMT)
TIMEVAL_FMT = TIMESPEC_FMT
TIMEVAL_SIZE = ustruct.calcsize(TIMEVAL_FMT)

CLOCK_MONOTONIC = 1
CLOCK_MONOTONIC_RAW = 4

librt = ffilib.open('librt')
_clock_gettime = librt.func('i', 'clock_gettime', 'ip')
_gettimeofday = librt.func('i', 'gettimeofday', 'ip')
_ts_buf = bytearray(TIMESPEC_SIZE)
_tv_buf = bytearray(TIMEVAL_SIZE)

def clock_gettimeofday(clk_id):
	e1 = _clock_gettime(clk_id, _ts_buf)
	e2 = _gettimeofday(_tv_buf, None)
	os.check_error(e1)
	os.check_error(e2)
	s, ns = ustruct.unpack(TIMESPEC_FMT, _ts_buf)
	utc_s, utc_us = ustruct.unpack(TIMEVAL_FMT, _tv_buf)
	return s, ns, utc_s, utc_us

def clock_gettime(clk_id):
	e = _clock_gettime(clk_id, _ts_buf)
	os.check_error(e)
	s, ns = ustruct.unpack(TIMESPEC_FMT, _ts_buf)
	return (s*10**9)+ns

def monotime():
	return clock_gettime(CLOCK_MONOTONIC)

def monotime_raw():
	return clock_gettime(CLOCK_MONOTONIC_RAW)
