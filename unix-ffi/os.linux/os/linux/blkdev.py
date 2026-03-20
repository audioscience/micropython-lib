
# buffer data from src_fobj (it could be the stdin pipe) before writing
# it out in blocksz bytes chunks
def block_copy(src_fobj, dst_fobj, byte_count = 0, blocksz=512, progress_func=None):
	buf = bytearray(blocksz)
	buf_view = memoryview(buf)
	read_bytes = 0
	left_to_read = byte_count
	total_written_bytes = 0
	while True:
		sz = src_fobj.readinto(buf_view[read_bytes:])
		#print('readinto() -> {}, {}'.format(read_bytes, sz), file=sys.stderr)
		# in blocking mode sz will be zero only on EOF
		left_to_read -= sz
		read_bytes += sz
		if not sz or (byte_count != 0 and left_to_read <= 0):
			cnt = read_bytes
			if byte_count != 0 and left_to_read <= 0:
				cnt += left_to_read
			dst_fobj.write(buf_view[:cnt])
			total_written_bytes += cnt
			if progress_func is not None:
				progress_func(total_written_bytes)
			break
		if read_bytes == blocksz:
			dst_fobj.write(buf_view)
			#print('write({})'.format(read_bytes), file=sys.stderr)
			total_written_bytes += read_bytes
			read_bytes = 0
			if progress_func is not None:
				progress_func(total_written_bytes)
