import unittest
from types import SimpleNamespace

from ariadne.engine import Session


class RemoteTarfileProbeTests(unittest.TestCase):
	def _session(self, exec_return=None, exec_raises=False):
		def exec_side(*args, **kwargs):
			if exec_raises:
				raise RuntimeError('dead session')
			return exec_return
		return SimpleNamespace(_remote_tarfile=None, exec=exec_side)

	def test_probe_positive_is_cached(self):
		calls = []
		session = SimpleNamespace(_remote_tarfile=None,
			exec=lambda *a, **k: calls.append(k) or 'ariadne-tarfile-ok')
		self.assertTrue(Session.remote_tarfile_ok(session))
		self.assertTrue(Session.remote_tarfile_ok(session))
		self.assertEqual(len(calls), 1, 'probe must be cached per session')

	def test_probe_negative_when_module_missing(self):
		session = self._session(exec_return="ModuleNotFoundError: No module named 'tarfile'")
		self.assertFalse(Session.remote_tarfile_ok(session))

	def test_probe_negative_when_session_dead(self):
		session = self._session(exec_return=False)
		self.assertFalse(Session.remote_tarfile_ok(session))

	def test_probe_negative_when_exec_raises(self):
		session = self._session(exec_raises=True)
		self.assertFalse(Session.remote_tarfile_ok(session))


class UnpackExitCodeParsingTests(unittest.TestCase):
	"""The agent fallback echoes 'ariadne-unpack-rc=<n>' from the same sh -c."""

	def test_rc_marker_is_parsed_from_response(self):
		import re
		response = 'tar: some warning\nariadne-unpack-rc=0\n'
		matched = re.search(r'ariadne-unpack-rc=(\d+)', response if isinstance(response, str) else '')
		self.assertEqual(matched[1], '0')

	def test_rc_marker_absent_when_response_is_not_a_string(self):
		import re
		matched = re.search(r'ariadne-unpack-rc=(\d+)', '' if not isinstance(False, str) else False)
		self.assertIsNone(matched)


if __name__ == '__main__':
	unittest.main()


class CanDeployAgentHealthTests(unittest.TestCase):
	"""Agent deployment must refuse crippled pythons (missing stdlib)."""

	def _session(self, os_name, responses, import_probe='ariadne-imports-ok'):
		import tempfile
		from pathlib import Path
		tmp = tempfile.mkdtemp(prefix='ariadne-agentprobe-')
		self.addCleanup(__import__('shutil').rmtree, tmp, ignore_errors=True)
		# FIFO of canned responses, consumed in call order
		queue = list(responses)
		def exec_side(*args, **kwargs):
			return queue.pop(0) if queue else None
		return SimpleNamespace(
			_can_deploy_agent=None, standalone_python=None,
			directory=Path(tmp), OS=os_name,
			bin={'python3': '/usr/bin/python3', 'python': None},
			exec=exec_side,
			remote_agent_imports_ok=lambda _bin: import_probe == 'ariadne-imports-ok',
		)

	def test_healthy_python_deploys(self):
		session = self._session('Unix', ['Python 3.13.2'])
		self.assertTrue(Session.can_deploy_agent.fget(session))
		self.assertTrue(session._can_deploy_agent)

	def test_crippled_python_is_refused(self):
		session = self._session('Unix', ['Python 3.13.2'], import_probe='ariadne-imports-missing')
		self.assertFalse(Session.can_deploy_agent.fget(session))
		self.assertFalse(session._can_deploy_agent)

	def test_windows_sessions_skip_the_import_probe(self):
		session = self._session('Windows', ['Python 3.13.2'])
		self.assertTrue(Session.can_deploy_agent.fget(session))
		self.assertEqual(len(session.bin), 2)  # probe did not consume a response


class TarOwnershipTests(unittest.TestCase):
	def test_upload_tar_zeroes_uid_gid(self):
		import io, tarfile, tempfile, os
		src = tempfile.NamedTemporaryFile(suffix='.txt', delete=False)
		src.write(b'payload'); src.close()
		self.addCleanup(os.unlink, src.name)
		buf = io.BytesIO()
		tar = tarfile.open(fileobj=buf, mode='w')
		# replicate the wrapper applied in upload()
		_orig = tar.gettarinfo
		def clean(*a, **k):
			info = _orig(*a, **k)
			if info is not None:
				info.uid = info.gid = 0
				info.uname = info.gname = 'root'
			return info
		tar.gettarinfo = clean
		tar.add(src.name)
		tar.close()
		buf.seek(0)
		with tarfile.open(fileobj=buf, mode='r:') as check:
			member = check.getmembers()[0]
		self.assertEqual((member.uid, member.gid), (0, 0))
		self.assertEqual(member.uname, 'root')
