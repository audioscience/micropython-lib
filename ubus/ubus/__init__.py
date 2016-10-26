
import os
import _ubus
import contextlib
import uasyncio
import logging
import functools
from . import uasyncio_utils


def check_error(retval):
	if retval == 0:
		return retval
	elif retval < 0:
		return os.check_error(retval)
	else:
		raise RuntimeError(retval)


_instances = {}

def _complete_handler(deferred, _ctx, _req, _ret):
	deferred.set_result(_ret)


def blob_len(blob_attr):
	return (blob_attr.id_len & 0x00ffffff) - uct.sizeof(blob_attr)


def _req_data_accumulator(_list):
	def _data_cb(_ctx, _req, _type, _msg):
		_list.append(_msg)
	return _data_cb


async def retry_ubus_func(peer, func, *args):
	while True:
		ret = func(*args)
		if ret == _ubus.UBUS_STATUS_OK or peer.conn.up:
			return ret
		logging.info('retry_ubus_func() %s %s returned %s', func, args, ret)
		await peer.conn.until_up()


async def call_once_conn_up(conn, func, *args):
	while True:
		try:
			if not conn.up:
				await conn.until_up()
			return await func(*args)
		except uasyncio.CancelledError:
			continue


async def call_once_object_available(peer, obj, func, *args):
	while True:
		try:
			await peer.waitfor_obj(obj)
			return await func(*args)
		except uasyncio.CancelledError:
			continue


def process_recv_data(ctx, ev_loop, fd):
	# process incoming messages
	ctx.process_recv_data()
	# re-arm poll fd
	ev_loop.add_reader(fd, process_recv_data, ctx, ev_loop, fd)


class RemoteObjectRemoved(Exception):
	pass


class UBusObjNotificationSubscriber:
	def __init__(self, ubus_peer, obj_proxy, subscriber_obj):
		self.peer = ubus_peer
		self.obj_proxy = obj_proxy
		self._subscribed = False
		self._subscriber_obj = subscriber_obj
		wq = uasyncio_utils.AFuture()
		self.notification_wq = wq
		self.obj_proxy._notification_wqs[wq] = wq

	async def __aenter__(self):
		await self.setup()
		return self

	async def __aexit__(self, exc_type, exc, tb):
		self.teardown()

	async def setup(self):
		return await call_once_object_available(self.peer, self.obj_proxy, self._setup)

	def teardown(self):
		self.obj_proxy._notification_wqs.pop(self.notification_wq, None)

	async def _setup(self):
		if self.notification_wq.cancelled or not self._subscribed:
			ctx = self.peer.conn.ctx
			# (re)subscribe to notification by object id
			ret = ctx.register_subscriber(self._subscriber_obj)
			if ret in (_ubus.UBUS_STATUS_CONNECTION_FAILED, _ubus.UBUS_STATUS_TIMEOUT, _ubus.UBUS_STATUS_NOT_FOUND):
				logging.error('register_subscriber() ret %s', ret)
				raise uasyncio.CancelledError
			check_error(ret)
			ret = ctx.subscribe(self._subscriber_obj, self.obj_proxy.obj_id)
			if ret in (_ubus.UBUS_STATUS_CONNECTION_FAILED, _ubus.UBUS_STATUS_TIMEOUT, _ubus.UBUS_STATUS_NOT_FOUND):
				logging.error('ctx.subscribe() ret %s', ret)
				raise uasyncio.CancelledError
			check_error(ret)
			self.notification_wq.reset()
			self._subscribed = True

	async def next_notification(self):
		if not self.notification_wq.cancelled and self.notification_wq.has_result():
			return self.notification_wq.result_nowait()
		# Subscribe if not subscribed
		if self.notification_wq.cancelled or not self._subscribed:
			await self.setup()
		# Wait for next notification
		return await self.notification_wq.result()


class UBusObjProxy:
	def __init__(self, ubus_peer, obj_path, obj_id, type_id):
		self.peer = ubus_peer
		self.path = obj_path
		self.type_id = type_id
		self.obj_id = obj_id
		self._subscribed = False
		self._subscriber_obj = _ubus.subscriber(self._handle_obj_notification)
		self._cancel_on_remove = {}
		self._notification_wqs = {}

	def is_stale(self):
		"""Return True if the remote object was removed at some point.

		This is useful to know in advance if a remote call may wait, possibly
		forever, for an object to become available again.
		"""
		return not self.peer.obj_cache.is_obj_cached(self)

	def update_id(self, obj_path, obj_id, type_id):
		if self.path != obj_path:
			raise ValueError('Identity of proxy cannot change {} -> {}'.format(self.path, obj_path))
		self.obj_id = obj_id
		self.type_id = type_id

	# cache known objects by proxy_obj's reference (.id() not ubus_id)
	# a proxy_obj's path is what identifies the object globally
	# so when a call is placed if the object .id() is not present it means
	# the object went away and we should wait for its *path* to come back
	# once the path is available again the new ubus_id is assigned to the
	# existing proxy_obj instance. If type_id has changed an exception is raised.

	def cancel_on_remove_nocontext(self, future):
		if self.is_stale():
			future.cancel()
		else:
			if future not in self._cancel_on_remove:
				self._cancel_on_remove[future] = future

	def cancel_on_remove_discard(self, future):
		self._cancel_on_remove.pop(future, None)

	@contextlib.contextmanager
	def cancel_on_remove(self, future):
		try:
			self.cancel_on_remove_nocontext(future)
			yield
		finally:
			self._cancel_on_remove.pop(future, None)

	def removed(self):
		for future in self._cancel_on_remove.values():
			future.cancel()
		self._cancel_on_remove.clear()
		# Cancelled but not removed
		for future in self._notification_wqs.values():
			future.cancel()

	async def invoke_method(self, method, data=None):
		try:
			return await call_once_object_available(self.peer, self, self._invoke_method, method, data)
		finally:
			self.peer.conn.ctx.process_pending()

	async def _invoke_method(self, method, data):
		conn = self.peer.conn
		ctx = self.peer.conn.ctx
		# FIXME: turn _complete_handler into a lambda
		with uasyncio_utils.AsyncCallback(_complete_handler) as req, \
			self.cancel_on_remove(req):
			try:
				res = []
				_req = _ubus.request(req.cb, _req_data_accumulator(res))
				ret = ctx.invoke_async(self.obj_id, method, data, _req)
				if ret in (_ubus.UBUS_STATUS_CONNECTION_FAILED, _ubus.UBUS_STATUS_TIMEOUT, _ubus.UBUS_STATUS_NOT_FOUND):
					raise uasyncio.CancelledError
				check_error(ret)
				ctx.complete_request_async(_req)
				status = await req.done()
				return (status, res, -1)
			except:
				ctx.abort_request(_req)
				raise

	# Notifications

	def _handle_obj_notification(self, _ctx, _sub, notification, msg):
		for wq in self._notification_wqs:
			wq.set_result((self, notification, [msg]))

	def notification_subscriber(self):
		return UBusObjNotificationSubscriber(self.peer, self, self._subscriber_obj)


class UBusObjInstance:
	def __init__(self, peer, ubus_obj_name, dispatch_func):
		self.peer = peer
		self._obj = _ubus.object(ubus_obj_name, dispatch_func)

	async def publish(self):
		return await call_once_conn_up(self.peer.conn, self._publish)

	async def _publish(self):
		ret = self.peer.conn.ctx.add_object(self._obj)
		if ret in (_ubus.UBUS_STATUS_CONNECTION_FAILED, _ubus.UBUS_STATUS_TIMEOUT):
			raise uasyncio.CancelledError
		check_error(ret)
		return ret

	async def withdraw(self):
		return await call_once_conn_up(self.peer.conn, self._withdraw)

	async def _withdraw(self):
		ret = self.peer.conn.ctx.remove_object(self._obj)
		if ret in (_ubus.UBUS_STATUS_CONNECTION_FAILED, _ubus.UBUS_STATUS_TIMEOUT):
			raise uasyncio.CancelledError
		check_error(ret)
		return ret

	def notify_subscribers(self, notification, msg):
		ret = self.peer.conn.ctx.notify(self._obj, notification, msg)
		check_error(ret)

	async def wait_request(self, payload):
		pass

	async def send_reply(self, request, payload):
		pass


class UBusObjProxyCache:
	def __init__(self):
		self._obj_by_path = {}
		self._ubusid_by_obj = {}
		self._obj_by_id = {}

	def is_obj_cached(self, proxy_obj):
		return proxy_obj in self._ubusid_by_obj

	def lookup(self, path):
		return self._obj_by_path.get(path, None)

	def lookup_id(self, _id):
		return self._obj_by_id.get(_id, None)

	def add(self, num_id, path, obj):
		assert path not in self._obj_by_path
		self._obj_by_path[path] = obj
		assert obj not in self._ubusid_by_obj
		self._ubusid_by_obj[obj] = num_id
		assert id not in self._obj_by_id
		self._obj_by_id[num_id] = obj

	def remove(self, path):
		obj = self._obj_by_path.get(path, None)
		if obj:
			del self._obj_by_path[path]
			del self._ubusid_by_obj[obj]
			del self._obj_by_id[obj.obj_id]
			obj.removed()
		return obj

	def flush(self):
		for obj in self._obj_by_path.values():
			obj.removed()
		self._obj_by_path.clear()
		self._ubusid_by_obj.clear()
		self._obj_by_id.clear()


class UBusConnection:
	def __init__(self, socket_path, peer_disconnect_cb, obj_event_handler=None):
		self.path = socket_path
		self.ctx = _ubus.ctx()
		self.up = False
		self.established = uasyncio_utils.AFuture()
		self.peer_disconnect_cb = peer_disconnect_cb
		self.lost = uasyncio_utils.AsyncCallback(self._disconnect_cb)
		self.lost.set_result(True)
		self.obj_event_handler = obj_event_handler
		self._cancel_on_disconnect = {}

	def _disconnect_cb(self, deferred, _ctx):
		# Hold everyone up until we are connected again
		self.up = False
		deferred.set_result(True)
		self.established.reset()
		self.peer_disconnect_cb()
		for future in self._cancel_on_disconnect.values():
			future.cancel()
		self._cancel_on_disconnect.clear()

	async def until_up(self):
		return await self.established.result(consume=False)

	async def until_down(self):
		return await self.lost.result(consume=False)

	def try_connect(self):
		ctx = self.ctx
		path = self.path
		ev_loop = uasyncio.get_event_loop()
		try:
			ret = ctx.connect(path, self.lost.cb)
			check_error(ret)
			if self.obj_event_handler:
				ret = ctx.register_event_handler(self.obj_event_handler, 'ubus.object.add')
				check_error(ret)
				ret = ctx.register_event_handler(self.obj_event_handler, 'ubus.object.remove')
				check_error(ret)
			logging.info('connecting to {} succeded'.format(path))
		except Exception as e:
			logging.error('connecting to {} failed: {}'.format(path, e))
			raise
		# Setup I/O callback
		ev_loop = uasyncio.get_event_loop()
		fd = ctx.fileno()
		ev_loop.add_reader(fd, process_recv_data, ctx, ev_loop, fd)
		# Signal we are connected
		self.up = True
		self.established.set_result(True)
		self.lost.reset()

	async def _retry_connect(self, retry_interval):
		while True:
			with contextlib.suppress(Exception):
				self.try_connect()
				break
			await uasyncio.sleep(retry_interval)
		return True

	async def connect_and_maintain(self, retry_interval=1):
		ctx = self.ctx
		path = self.path
		connection_lost = self.lost.result
		ev_loop = uasyncio.get_event_loop()
		try:
			while True:
				if not self.up:
					# Keep trying to reconnect until we succeed
					await self._retry_connect(retry_interval)
				# Wait until connection is severed
				if not await connection_lost():
					continue
				logging.info('disconnected from {}'.format(path))
				# Stop polling the socket's file descriptor
				ev_loop.remove_reader(ctx.fileno(), process_recv_data)
		finally:
			with contextlib.suppress(KeyError):
				uasyncio.get_event_loop().remove_reader(ctx.fileno(), process_recv_data)
			if self.obj_event_handler:
				ctx.unregister_event_handler(self.obj_event_handler)
			ctx.shutdown()
			self.established.reset()

	def cancel_on_disconnect_nocontext(self, future):
		if not self.up:
			future.cancel()
		else:
			if future not in self._cancel_on_disconnect:
				self._cancel_on_disconnect[future] = future

	def cancel_on_disconnect_discard(self, future):
		self._cancel_on_disconnect.pop(future, None)

	@contextlib.contextmanager
	def cancel_on_disconnect(self, future):
		try:
			self.cancel_on_disconnect_nocontext(future)
			yield
		finally:
			self._cancel_on_disconnect.pop(future, None)


class UBusEventSubscriber:
	def __init__(self, peer):
		self.peer = peer
		self._ev_handler = _ubus.event_handler(self._ev_callback)
		self.patterns = []
		self.ev_q = uasyncio_utils.AFuture()
		peer.conn.cancel_on_disconnect_nocontext(self.ev_q)

	def _ev_callback(self, _ctx, _ev, ev, msg):
		self.ev_q.set_result((ev, msg))

	async def _refresh_registrations(self):
		for pattern in self.patterns:
			ret = self.peer.conn.ctx.register_event_handler(self._ev_handler, pattern)
			if ret in (_ubus.UBUS_STATUS_CONNECTION_FAILED, _ubus.UBUS_STATUS_TIMEOUT):
				raise uasyncio.CancelledError
			check_error(ret)

	async def register_pattern(self, pattern):
		return await call_once_conn_up(self.peer.conn, self._register_pattern, pattern)

	async def _register_pattern(self, pattern):
		if pattern in self.patterns:
			return
		conn = self.peer.conn
		conn.cancel_on_disconnect_nocontext(self.ev_q)
		if self.ev_q.cancelled:
			await self._refresh_registrations()
			self.ev_q.reset()
			conn.cancel_on_disconnect_nocontext(self.ev_q)
		ret = conn.ctx.register_event_handler(self._ev_handler, pattern)
		if ret in (_ubus.UBUS_STATUS_CONNECTION_FAILED, _ubus.UBUS_STATUS_TIMEOUT):
			raise uasyncio.CancelledError
		check_error(ret)
		self.patterns.append(pattern)

	async def event(self):
		return await call_once_conn_up(self.peer.conn, self._event)

	async def _event(self):
		if not self.ev_q.cancelled and self.ev_q.has_result():
			return self.ev_q.result_nowait()
		conn = self.peer.conn
		conn.cancel_on_disconnect_nocontext(self.ev_q)
		if self.ev_q.cancelled:
			await self._refresh_registrations()
			self.ev_q.reset()
			conn.cancel_on_disconnect_nocontext(self.ev_q)
		return await self.ev_q.result()

	def close(self):
		self.patterns.clear()
		self.peer.conn.cancel_on_disconnect_discard(self.ev_q)
		ret = self.peer.conn.ctx.unregister_event_handler(self._ev_handler)
		# Ignore failures to unregister to avoid errors on shutdown
		if ret in (_ubus.UBUS_STATUS_CONNECTION_FAILED, _ubus.UBUS_STATUS_TIMEOUT):
			return
		check_error(ret)


class UBusPeer:
	def __init__(self, socket_path):
		ev = _ubus.event_handler(self._handle_obj_event)
		self.conn = UBusConnection(socket_path, self._disconnect_cb, obj_event_handler=ev)
		self.obj_cache = UBusObjProxyCache()
		self._run_task = None
		self._waiting_objs = {}

	# Peer lifecycle management

	def __enter__(self):
		self.conn.try_connect()
		self.run()
		return self

	def __exit__(self, type, value, tb):
		self.shutdown()

	async def __aenter__(self):
		self.run()
		await self.conn.until_up()
		return self

	async def __aexit__(self, type, value, tb):
		self.shutdown()

	def try_connect(self):
		self.conn.try_connect()

	def run(self, connect_retry_interval=1):
		ubus_connect = self.conn.connect_and_maintain(retry_interval=connect_retry_interval)
		self._run_task = uasyncio.ensure_future(ubus_connect)

	def shutdown(self):
		if self._run_task:
			self._run_task.close()

	def _disconnect_cb(self):
		# Make all circulating proxy objs stale
		self.obj_cache.flush()
		# Wake up anyone waiting for a path with an exception
		for wq_list in self._waiting_objs.values():
			for wq in wq_list:
				wq.cancel()
		self._waiting_objs.clear()

	# Object lookup and caching

	def _handle_obj_event(self, _ctx, _ev, ev, msg):
		attrs = dict(_ubus.blob_decode(msg))
		path = attrs['path']
		if ev == 'ubus.object.add':
			self._process_obj_added(path)
		elif ev == 'ubus.object.remove':
			self._process_obj_removed(path)
		else:
			logging.error('Unexpected object event: %s', attrs)

	def _process_obj_added(self, path):
		# FIXME: error on adding a wait queue when the connection is down
		wait_queues = self._waiting_objs.get(path, ())
		for q in wait_queues:
			q.set_result(path)
		if wait_queues:
			del self._waiting_objs[path]

	def _process_obj_removed(self, path):
		self.obj_cache.remove(path)

	async def _lookup(self, path):
		if path.endswith('*'):
			raise ValueError('wildcard lookup not implemented')
		lookup_cb = lambda f,r:f.set_result(r)
		conn = self.conn
		with uasyncio_utils.AsyncCallback(lookup_cb) as lookup_future, \
				conn.cancel_on_disconnect(lookup_future):
			ret = conn.ctx.lookup(path, lookup_future.cb)
			if ret == _ubus.UBUS_STATUS_CONNECTION_FAILED:
				raise uasyncio.CancelledError
			if ret == _ubus.UBUS_STATUS_NOT_FOUND:
				return None
			check_error(ret)
			return await lookup_future.result()

	async def lookup(self, path, proxy_factory = UBusObjProxy):
		cached = self.obj_cache.lookup(path)
		if cached:
			return cached
		# not cached, look it up on the server
		res = await call_once_conn_up(self.conn, self._lookup, path)
		if not res:
			return res
		# create proxy object and cache it
		o = proxy_factory(self, *res)
		path, num_id, type_id = res
		self.obj_cache.add(num_id, path, o)
		return o

	# Event management
	async def send_event(self, event, payload):
		try:
			return await call_once_conn_up(self.conn, self._send_event, event, payload)
		finally:
			self.conn.ctx.process_pending()

	async def _send_event(self, event, payload):
		conn = self.conn
		ctx = self.conn.ctx
		ret = ctx.send_event(event, payload)
		if ret == _ubus.UBUS_STATUS_CONNECTION_FAILED:
			raise uasyncio.CancelledError
		check_error(ret)

	@contextlib.contextmanager
	def event_subscriber(self):
		ev_sub = UBusEventSubscriber(self)
		try:
			yield ev_sub
		finally:
			ev_sub.close()

	async def waitfor_paths(self, obj_paths):
		return await call_once_conn_up(self.conn, self._waitfor_paths, obj_paths)

	async def _waitfor_paths(self, obj_paths):
		conn = self.conn
		with uasyncio_utils.AFuture() as wq, \
				conn.cancel_on_disconnect(wq):
			for path in obj_paths:
				wq_set = self._waiting_objs.setdefault(path, set())
				wq_set.add(wq)
			outstanding = set(obj_paths)
			for path in obj_paths:
				res = await self._lookup(path)
				if res:
					assert res[0] == path
					outstanding.remove(path)
					self._waiting_objs.get(path, set()).discard(wq)
			while outstanding:
				path = await wq.result()
				outstanding.discard(path)
		return obj_paths

	async def waitfor_obj(self, obj_proxy):
		return await call_once_conn_up(self.conn, self._waitfor_obj, obj_proxy)

	async def _waitfor_obj(self, obj_proxy):
		while not self.obj_cache.is_obj_cached(obj_proxy):
			res = await self._lookup(obj_proxy.path)
			if not res:
				await self._waitfor_paths([obj_proxy.path])
				continue
			obj_proxy.update_id(*res)
			path, num_id, type_id = res
			self.obj_cache.add(num_id, path, obj_proxy)
			break
		return obj_proxy

	# Local object creation

	def object(self, ubus_obj_name, dispatch_func):
		return UBusObjInstance(self, ubus_obj_name, dispatch_func)

	# Remote method invokation is handled by the obj proxy


def peer(socket_path=None):
	"""
	>>> p.peer()
	>>> try:
	>>>     p.startup()
	>>>     # do something with p
	>>> finally:
	>>>     p.shutdown()

	or

	>>> with peer() as p:
	>>>     # do something with p
	>>>
	"""
	if socket_path not in _instances:
		_instances[socket_path] = UBusPeer(socket_path)
	return _instances[socket_path]


async def connected_peer(socket_path=None):
	"""
	>>> try:
	>>>     p = await connected_peer()
	>>>     # do something with p
	>>> finally:
	>>>     p.shutdown()
	"""
	p = peer(socket_path=socket_path).startup()
	await p.conn.until_up()
	return p
