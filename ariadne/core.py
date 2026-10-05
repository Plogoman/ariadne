#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *
from .options import options
from .log import logger, cmdlogger
from .display import LineBuffer, signal_level
from .network import ControlQueue, TCPListener
from .cli_input import stdout
from .engine import Session, Messenger

# menu: injected by ariadne/__init__.py after the singleton exists.

# Bracketed paste: some terminals wrap pasted text in \x1b[200~ / \x1b[201~
# markers (so apps can tell typed input from pasted text), so strip them
# before forwarding stdin to a Raw session. PTY sessions are deliberately
# left alone: the remote turns bracketed paste on itself and relies on the
# markers arriving intact.
BRACKETED_PASTE_RE = re.compile(rb'\x1b\[20[01]~')
_BP_MARKER_PREFIXES = tuple(b'\x1b[201~'[:n] for n in range(5, 0, -1)) \
	+ tuple(b'\x1b[200~'[:n] for n in range(5, 0, -1))

def strip_bracketed_paste(data, state):
	"""Remove paste markers from `data`. `state` is a one-element list holding
	bytes carried over from the previous read (a marker split across reads)."""
	data = state[0] + data
	state[0] = b''
	data = BRACKETED_PASTE_RE.sub(b'', data)
	for prefix in _BP_MARKER_PREFIXES:
		if data.endswith(prefix):
			state[0] = prefix
			data = data[:-len(prefix)]
			break
	return data


class Core:

	def __init__(self):
		self.started = False
		self.control = ControlQueue()
		self.rlist = [self.control]
		self.wlist = []

		self.attached_session = None
		self.conn_semaphore = threading.Semaphore(5)

		self.listener_counter = itertools.count(1)
		self.session_counter = itertools.count(1)
		self.fileserver_counter = itertools.count(1)
		self.forwarding_counter = itertools.count(1)
		self.counter_lock = threading.Lock()

		self.sessions = {}
		self.listeners = {}
		self.fileservers = {}
		self.forwardings = {}

		self.output_line_buffer = LineBuffer(1)
		self._paste_state = [b'']
		self.wait_input = False

	def __getattr__(self, name):

		if name == 'new_listenerID':
			with self.counter_lock:
				return next(self.listener_counter)

		elif name == 'new_sessionID':
			with self.counter_lock:
				while True:
					candidate = f"{choice(SESSION_NAME_ADJECTIVES)}-{choice(SESSION_NAME_NOUNS)}"
					if candidate not in self.sessions:
						return candidate

		elif name == 'new_session_ordinal':
			with self.counter_lock:
				return next(self.session_counter)

		elif name == 'new_fileserverID':
			with self.counter_lock:
				return next(self.fileserver_counter)

		elif name == 'new_forwardingID':
			with self.counter_lock:
				return next(self.forwarding_counter)
		else:
			raise AttributeError(name)

	@property
	def hosts(self):
		result = {}
		for session in list(self.sessions.values()):
			name = getattr(session, 'name', None)
			if name:
				result.setdefault(name, []).append(session)
		return result

	@property
	def threads(self):
		return [thread.name for thread in threading.enumerate()]

	def start(self):
		self.started = True
		threading.Thread(target=self.loop, name="Core").start()
		threading.Thread(target=self.sample_signals, name="SignalSampler", daemon=True).start()

	def sample_signals(self):
		prev = {}
		while self.started:
			for session in list(self.sessions.values()):
				try:
					sig = session.tcp_signal()
					if sig is not None:
						rtt, jitter, retrans = sig
						loss = retrans > prev.get(session.id, retrans)
						prev[session.id] = retrans
					elif session.latency is not None:
						rtt, jitter, loss = session.latency * 1000.0, 0, False
					else:
						continue
					session.rtt_ms    = rtt    if session.rtt_ms    is None else 0.7 * session.rtt_ms    + 0.3 * rtt
					session.jitter_ms = jitter if session.jitter_ms is None else 0.7 * session.jitter_ms + 0.3 * jitter
					session.signal = signal_level(session.rtt_ms, loss, session.jitter_ms)
				except Exception:
					continue
			for sid in set(prev) - set(self.sessions):
				prev.pop(sid, None)
			time.sleep(2)

	def loop(self):

		while self.started:
			try:
				readables, writables, _ = select(self.rlist, self.wlist, [])
			except (ValueError, OSError):
				def _valid_fd(x):
					try:
						return x.fileno() >= 0
					except Exception:
						return False
				for lst in (self.rlist, self.wlist):
					for x in tuple(lst):
						if not _valid_fd(x):
							try:
								lst.remove(x)
							except ValueError:
								pass
				continue

			for readable in readables:

				# The control queue
				if readable is self.control:
					command = self.control.get()
					if command:
						try:
							command()
						except KeyError:
							logger.debug("The session does not exist anymore")
						except Exception as e:
							logger.error(f"Core Control command failed: {e}")
					else:
						logger.debug("Core break")
					break

				# The listeners
				elif readable.__class__ is TCPListener:
					try:
						_socket, endpoint = readable.socket.accept()
					except BlockingIOError:
						continue
					except OSError:
						continue
					if sum(1 for s in list(self.sessions.values()) if s.ip == endpoint[0]) >= options.max_sessions:
						_socket.close()
						logger.debug(f"Rejected {endpoint}: max sessions per host ({options.max_sessions}) reached")
						continue
					thread_name = f"NewCon{endpoint}"
					logger.debug(f"New thread: {thread_name}")
					# Daemon: see the matching comment in network.Connect(); a
					# half-finished session setup must not stall interpreter
					# shutdown.
					threading.Thread(target=Session, args=(_socket, *endpoint, readable), name=thread_name, daemon=True).start()

				# STDIN
				elif readable is sys.stdin:
					if self.attached_session:
						session = self.attached_session
						if session.type == 'Readline':
							continue

						data = os.read(sys.stdin.fileno(), options.network_buffer_size)

						# Only Raw sessions need this. A PTY target turns bracketed paste
						# on itself and relies on the markers arriving intact, e.g. vim
						# suppressing auto-indent, bash not running a pasted newline.
						if session.type == 'Raw' and not options.keep_bracketed_paste:
							data = strip_bracketed_paste(data, self._paste_state)
							if not data:
								continue

						if session.subtype == 'cmd':
							self._cmd = data

						if data == options.escape['sequence']:
							#if session.alternate_buffer:
							#	logger.error("(!) Exit the current alternate buffer program first")
							#else:
							#	session.detach()
							session.detach()
						else:
							if session.type == 'Raw':
								session.record(data, _input=True)

							elif session.agent:
								data = Messenger.message(Messenger.SHELL, data)

							session.send(data, stdin=True)
					else:
						logger.error("You shouldn't see this error; Please report it")

				# The sessions
				elif readable.__class__ is Session:
					try:
						data = readable.socket.recv(options.network_buffer_size)
						if not data:
							raise OSError
					except BlockingIOError:
						continue
					except OSError:
						logger.debug("Died while reading")
						readable.kill()
						break
					readable.bytes_received += len(data)

					with readable.data_route_lock:
						# kill() closes the subchannel before Core removes the
						# session from rlist, so writing routed data can hit a
						# dead pipe; die quietly instead of crashing the loop.
						try:
							target = readable.subchannel\
							if readable.subchannel.active\
							else readable.shell_response_buf

							if readable.agent:
								for _type, _value in readable.messenger.feed(data):
									#print(_type, _value)
									if _type == Messenger.SHELL:
										if not _value: # TEMP
											readable.kill()
											break
										target.write(_value)

									elif _type == Messenger.STREAM:
										stream_id, stream_data = _value[:Messenger.STREAM_BYTES], _value[Messenger.STREAM_BYTES:]
										#print((repr(stream_id), repr(stream_data)))
										try:
											readable.streams[stream_id] << stream_data
										except (OSError, KeyError):
											logger.debug(f"Cannot write to stream; Stream <{stream_id}> died prematurely")
							else:
								target.write(data)
						except OSError:
							readable.kill()
							break

					shell_output = readable.shell_response_buf.getvalue() # TODO
					if shell_output:
						if readable.is_attached:
							stdout(shell_output)

						readable.record(shell_output)

						#if b'\x1b[?1049h' in shell_output:
						#	readable.alternate_buffer = True

						#if b'\x1b[?1049l' in shell_output:
						#	readable.alternate_buffer = False

						#if readable.subtype == 'cmd' and self._cmd == data:
						#	data, self._cmd = b'', b'' # TODO

						readable.shell_response_buf.seek(0)
						readable.shell_response_buf.truncate(0)

			for writable in writables:
				with writable.wlock:
					try:
						sent = writable.socket.send(writable.outbuf.getvalue())
						writable.bytes_sent += sent
					except BlockingIOError:
						continue
					except OSError:
						logger.debug("Died while writing")
						writable.kill()
						break

					writable.outbuf.seek(sent)
					remaining = writable.outbuf.read()
					writable.outbuf.seek(0)
					writable.outbuf.truncate()
					writable.outbuf.write(remaining)
					if not remaining:
						self.wlist.remove(writable)

	def stop(self):
		options.maintain = 0

		if self.sessions:
			logger.warning("Killing sessions...")
			for session in reversed(list(self.sessions.values())):
				session.kill()

		for listener in list(self.listeners.values()):
			listener.stop()

		for fileserver in list(self.fileservers.values()):
			fileserver.stop()

		self.control << (lambda: setattr(self, 'started', False))

		menu.stop = True
		menu.cmdqueue.append("")
		menu.active.set()
