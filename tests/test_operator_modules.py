import unittest
import importlib
from types import SimpleNamespace
from unittest.mock import patch

from ariadne.display import LineBuffer, Open, Table
from ariadne import engine
from ariadne.payload_data import URLS, PYTHON_STANDALONE_BINARIES
from ariadne.utils import url_to_bytes
from ariadne.fileserver import FileServer
from ariadne.modules import certipy, ensure_target_binaries, enum4linux_ng, meterpreter, run_operator_tool, seatbelt, upload_privesc_scripts
from ariadne.modules import upload_k8s_tools, upload_single_from_archive, virt_enum


class OperatorModuleTests(unittest.TestCase):
	def test_engine_imports_line_buffer_for_new_sessions(self):
		self.assertIs(engine.LineBuffer, LineBuffer)

	def test_engine_imports_payload_data_for_agent_fallbacks(self):
		self.assertIs(engine.URLS, URLS)
		self.assertIs(engine.PYTHON_STANDALONE_BINARIES, PYTHON_STANDALONE_BINARIES)

	def test_split_modules_have_their_runtime_dependencies(self):
		cli_input = importlib.import_module('ariadne.cli_input')
		display = importlib.import_module('ariadne.display')
		modules = importlib.import_module('ariadne.modules')
		self.assertIs(cli_input.Table, Table)
		self.assertIs(display.readline, cli_input.readline)
		self.assertIs(engine.Open, Open)
		self.assertIs(engine.FileServer, FileServer)
		self.assertIs(engine.load_rc, importlib.import_module('ariadne').load_rc)
		self.assertIs(modules.url_to_bytes, url_to_bytes)

	@patch('ariadne.modules.run_operator_tool')
	def test_certipy_always_runs_find_with_parsed_arguments(self, run_tool):
		session = SimpleNamespace()
		certipy.run(session, '-u analyst@example.test -dc-ip 192.0.2.10')
		run_tool.assert_called_once_with('Certipy', ('certipy', 'certipy-ad'), [
			'find', '-u', 'analyst@example.test', '-dc-ip', '192.0.2.10'])

	@patch('ariadne.modules.run_operator_tool')
	def test_enum4linux_defaults_to_selected_session_ip(self, run_tool):
		session = SimpleNamespace(ip='192.0.2.20')
		enum4linux_ng.run(session, '')
		run_tool.assert_called_once_with('enum4linux-ng',
			('enum4linux-ng', 'enum4linux-ng.py'), ['192.0.2.20', '-A'])

	@patch('ariadne.modules.subprocess.run')
	@patch('ariadne.modules.shutil.which', return_value='/usr/bin/enum4linux-ng')
	def test_operator_tool_uses_argv_without_a_shell(self, which, run):
		run.return_value = SimpleNamespace(returncode=0)
		self.assertTrue(run_operator_tool('enum4linux-ng', ('enum4linux-ng',),
			['192.0.2.20', '-A']))
		run.assert_called_once_with(['/usr/bin/enum4linux-ng', '192.0.2.20', '-A'], check=False)

	@patch('ariadne.modules.rand', return_value='abcdefgh')
	def test_seatbelt_uses_uploaded_ghostpack_and_downloads_json(self, rand):
		session = SimpleNamespace(
			OS='Windows', subtype='cmd',
			uploaded_paths={'"C:\\Temp\\ghostpack"': 0},
			tmp='C:\\Windows\\Temp',
			exec=unittest.mock.Mock(return_value='completed'),
			download=unittest.mock.Mock(return_value=['/tmp/session/downloads/report.json']),
		)
		seatbelt.run(session, '--group user --full')
		command = session.exec.call_args_list[0].args[0]
		self.assertIn('"C:\\Temp\\ghostpack\\Seatbelt.exe"', command)
		self.assertIn('-group=user -q', command)
		self.assertIn('-full', command)
		session.download.assert_called_once()


def _target_responder(present_binaries, install_calls, provides=None):
	def responder(command, **kwargs):
		if 'command -v' in command:
			binary = command.split('command -v ', 1)[1].split(' ', 1)[0]
			if binary in present_binaries:
				return 'ariadne-present'
			return 'ariadne-missing'
		if ' add ' in command or 'install' in command:
			install_calls.append(command)
			for fragment, binary in (provides or {}).items():
				if fragment in command:
					present_binaries.add(binary)
			return 'installed'
		return ''
	return responder


class UploadPrivescScriptsTests(unittest.TestCase):
	def test_every_tool_has_a_download_url(self):
		virtual_names = {'pspy': ('pspy64', 'pspy32'), 'winpeas': ('winpeas_any',)}
		for tool in {*upload_privesc_scripts.unix_tools, *upload_privesc_scripts.windows_tools}:
			for url_key in virtual_names.get(tool, (tool,)):
				self.assertIn(url_key, URLS, f"upload_privesc_scripts tool '{tool}' has no URLS entry")
		self.assertLessEqual(upload_privesc_scripts.default_skip,
			set(upload_privesc_scripts.unix_tools) | set(upload_privesc_scripts.windows_tools))

	def test_unix_tools_upload_from_payload_urls(self):
		uploads = []
		session = SimpleNamespace(OS='Unix', arch='x86_64', cwd='/tmp', write_access=lambda cwd: True,
			upload=lambda url, **kwargs: uploads.append(url))
		upload_privesc_scripts.run(session, 'linenum amicontained cdk')
		self.assertEqual(uploads, [URLS['linenum'], URLS['amicontained'], URLS['cdk']])

	def test_windows_tools_upload_from_payload_urls(self):
		uploads = []
		session = SimpleNamespace(OS='Windows', arch='x64-based_PC', cwd='C:\\Temp', write_access=lambda cwd: True,
			upload=lambda url, **kwargs: uploads.append(url))
		upload_privesc_scripts.run(session, 'sharpup sharpdpapi')
		self.assertEqual(uploads, [URLS['sharpup'], URLS['sharpdpapi']])

	def test_cross_os_requests_are_rejected_without_uploading(self):
		uploads = []
		session = SimpleNamespace(OS='Unix', arch='x86_64', cwd='/tmp', write_access=lambda cwd: True,
			upload=lambda url, **kwargs: uploads.append(url))
		upload_privesc_scripts.run(session, 'winpeas sharpup')
		self.assertEqual(uploads, [])

	def test_no_arguments_uploads_everything_except_default_skip(self):
		uploads = []
		session = SimpleNamespace(OS='Unix', arch='x86_64', cwd='/tmp', write_access=lambda cwd: True,
			upload=lambda url, **kwargs: uploads.append(url))
		upload_privesc_scripts.run(session, '')
		expected = [URLS['linpeas'], URLS['lse'], URLS['linenum'], URLS['les'],
			URLS['deepce'], URLS['pspy64'], URLS['amicontained']]
		self.assertEqual(uploads, expected)  # 'cdk' is default-skipped


class EnsureTargetBinariesTests(unittest.TestCase):
	def test_nothing_runs_when_binary_already_present(self):
		calls = []
		session = SimpleNamespace(OS='Unix', exec=_target_responder({'ps', 'apk'}, calls))
		with patch('ariadne.modules.ask') as ask:
			self.assertTrue(ensure_target_binaries(session, {'ps': 'procps'}))
		ask.assert_not_called()
		self.assertEqual(calls, [])

	def test_windows_sessions_are_skipped(self):
		execs = unittest.mock.Mock()
		session = SimpleNamespace(OS='Windows', exec=execs)
		self.assertTrue(ensure_target_binaries(session, {'ps': 'procps'}))
		execs.assert_not_called()

	@patch('ariadne.modules.ask', return_value='y')
	def test_missing_binary_is_installed_via_detected_package_manager(self, ask):
		calls = []
		session = SimpleNamespace(OS='Unix', cwd='/tmp', exec=_target_responder({'apk'}, calls,
			provides={'procps': 'ps'}))
		self.assertTrue(ensure_target_binaries(session, {'ps': 'procps'}))
		ask.assert_called_once()
		self.assertEqual(len(calls), 1)
		self.assertIn('apk add --no-cache procps', calls[0])

	@patch('ariadne.modules.ask', return_value='n')
	def test_declining_the_prompt_skips_the_install(self, ask):
		calls = []
		session = SimpleNamespace(OS='Unix', exec=_target_responder({'apk'}, calls))
		self.assertFalse(ensure_target_binaries(session, {'ps': 'procps'}))
		ask.assert_called_once()
		self.assertEqual(calls, [])

	def test_no_package_manager_reports_missing_without_prompting(self):
		calls = []
		session = SimpleNamespace(OS='Unix', exec=_target_responder(set(), calls))
		with patch('ariadne.modules.ask') as ask:
			self.assertFalse(ensure_target_binaries(session, {'ps': 'procps'}))
		ask.assert_not_called()
		self.assertEqual(calls, [])


if __name__ == '__main__':
	unittest.main()


class VirtEnumTests(unittest.TestCase):
	def test_prints_target_output(self):
		session = SimpleNamespace(OS='Unix', exec=lambda *a, **k: 'ContainerRuntime: docker\n')
		with patch('builtins.print') as fake_print:
			virt_enum.run(session, '')
		fake_print.assert_called_once_with('ContainerRuntime: docker\n')

	def test_cloud_flag_triggers_metadata_probe(self):
		calls = []
		session = SimpleNamespace(OS='Unix', exec=lambda cmd, **k: calls.append(cmd) or 'ok')
		with patch('builtins.print'):
			virt_enum.run(session, 'cloud')
		self.assertEqual(len(calls), 2)
		self.assertIn('169.254.169.254', calls[1])

	def test_windows_sessions_rejected(self):
		session = SimpleNamespace(OS='Windows', exec=unittest.mock.Mock())
		virt_enum.run(session, '')
		session.exec.assert_not_called()


class UploadK8sToolsTests(unittest.TestCase):
	def test_every_tool_has_a_download_url(self):
		for tool in ('peirates', 'kubeletctl'):
			self.assertIn(tool, URLS)

	@patch('ariadne.modules.url_to_bytes')
	def test_peirates_tarxz_archive_is_supported(self, mock_url):
		import io, tarfile as _tarfile
		buf = io.BytesIO()
		with _tarfile.open(fileobj=buf, mode='w:xz') as tf:
			info = _tarfile.TarInfo('peirates')
			payload = b'#!/bin/sh\nbinary\n'
			info.size = len(payload)
			tf.addfile(info, io.BytesIO(payload))
		mock_url.return_value = ('peirates-linux-amd64.tar.xz', buf.getvalue())
		captured = {}
		session = SimpleNamespace(upload=lambda url, url_to_bytes_fn=None, remote_path=None:
			captured.update(url=url, fn=url_to_bytes_fn))
		upload_single_from_archive(session, 'http://x/peirates-linux-amd64.tar.xz', 'peirates', 'peirates')
		_, data = captured['fn']('http://x/peirates-linux-amd64.tar.xz')
		self.assertEqual(data, b'#!/bin/sh\nbinary\n')


class MeterpreterLinuxTests(unittest.TestCase):
	def _unix_session(self, arch='x86_64'):
		return SimpleNamespace(
			OS='Unix', arch=arch, exec_tmp='/tmp', tmp='/tmp',
			listener=None, _host='172.17.0.1', subtype=None,
			exec=unittest.mock.Mock(
				side_effect=lambda cmd, **k: arch if 'uname -m' in cmd else None),
			upload=unittest.mock.Mock(return_value=['/tmp/payload.elf']),
		)

	def _windows_session(self):
		return SimpleNamespace(
			OS='Windows', arch='x64-based_PC', subtype='cmd', tmp='C:\\Temp',
			listener=None, _host='192.0.2.10',
			exec=unittest.mock.Mock(),
			upload=unittest.mock.Mock(return_value=['C:\\Temp\\p.exe']),
		)

	def _patched(self, arch='x86_64'):
		from unittest.mock import MagicMock
		subprocess_mock = MagicMock(return_value=SimpleNamespace(returncode=0, stderr=''))
		fake_open = MagicMock()
		which = MagicMock(return_value='/usr/bin/msfvenom')
		return subprocess_mock, fake_open, which

	def test_linux_x64_builds_elf_payload_and_launches_detached(self):
		from unittest.mock import patch, MagicMock
		subprocess_mock = MagicMock(return_value=SimpleNamespace(returncode=0, stderr=''))
		fake_open = MagicMock()
		session = self._unix_session('x86_64')
		with patch('ariadne.modules.shutil.which', MagicMock(return_value='/usr/bin/msfvenom')), \
			patch('ariadne.modules.subprocess.run', subprocess_mock), \
			patch('ariadne.modules.Open', fake_open), \
			patch('ariadne.modules.socket.socket') as fake_sock:
			fake_sock.return_value.bind.return_value = None
			meterpreter.run(session, '')
		msfvenom_cmd = subprocess_mock.call_args_list[0].args[0]
		self.assertIn('linux/x64/meterpreter/reverse_tcp', msfvenom_cmd)
		self.assertIn('elf', msfvenom_cmd)
		session.upload.assert_called_once()
		exec_cmds = [c.args[0] for c in session.exec.call_args_list]
		self.assertTrue(any(c.startswith('chmod +x ') for c in exec_cmds))
		self.assertTrue(any('nohup' in c and c.endswith('&') for c in exec_cmds))
		self.assertIn('linux/x64/meterpreter/reverse_tcp', fake_open.call_args.args[0])

	def test_linux_arch_mappings(self):
		from unittest.mock import patch, MagicMock
		subprocess_mock = MagicMock(return_value=SimpleNamespace(returncode=0, stderr=''))
		session = self._unix_session('i686')
		with patch('ariadne.modules.shutil.which', MagicMock(return_value='/usr/bin/msfvenom')), \
			patch('ariadne.modules.subprocess.run', subprocess_mock), \
			patch('ariadne.modules.socket.socket') as fake_sock:
			fake_sock.return_value.bind.return_value = None
			meterpreter.run(session, '')
			self.assertIn('linux/x86/meterpreter/reverse_tcp', subprocess_mock.call_args_list[0].args[0])
			session2 = self._unix_session('aarch64')
			meterpreter.run(session2, '')
			self.assertIn('linux/aarch64/meterpreter/reverse_tcp', subprocess_mock.call_args_list[1].args[0])

	def test_unsupported_arch_refuses_without_msfvenom(self):
		from unittest.mock import patch, MagicMock
		subprocess_mock = MagicMock(return_value=SimpleNamespace(returncode=0, stderr=''))
		session = self._unix_session('mips')
		with patch('ariadne.modules.shutil.which', MagicMock(return_value='/usr/bin/msfvenom')), \
			patch('ariadne.modules.subprocess.run', subprocess_mock), \
			patch('ariadne.modules.socket.socket') as fake_sock:
			fake_sock.return_value.bind.return_value = None
			meterpreter.run(session, '')
		subprocess_mock.assert_not_called()
		session.upload.assert_not_called()

	def test_windows_flow_unchanged(self):
		from unittest.mock import patch, MagicMock
		subprocess_mock = MagicMock(return_value=SimpleNamespace(returncode=0, stderr=''))
		fake_open = MagicMock()
		session = self._windows_session()
		with patch('ariadne.modules.shutil.which', MagicMock(return_value='/usr/bin/msfvenom')), \
			patch('ariadne.modules.subprocess.run', subprocess_mock), \
			patch('ariadne.modules.Open', fake_open), \
			patch('ariadne.modules.socket.socket') as fake_sock:
			fake_sock.return_value.bind.return_value = None
			meterpreter.run(session, '')
		self.assertIn('windows/x64/meterpreter/reverse_tcp', subprocess_mock.call_args_list[0].args[0])
		self.assertIn('start /b', session.exec.call_args_list[0].args[0])
