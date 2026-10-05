#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *
from .options import options
from .log import logger, cmdlogger
from .engine import Messenger

# core: injected by ariadne/__init__.py after the singleton exists.

class MCPServer:
	PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
	MAX_REQUEST = 1024 * 1024
	MAX_OUTPUT = 10 * 1024 ** 2
	MAX_RESPONSE = 10 * 1024 ** 2

	TOOLS = [
		{'name': 'list_sessions',
		 'description': 'List all active reverse-shell sessions in Ariadne.',
		 'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
		 'annotations': {'title': 'List sessions', 'readOnlyHint': True, 'openWorldHint': True}},
		{'name': 'get_session_info',
		 'description': 'Detailed info about one session (OS, shell, user, hostname, cwd, arch).',
		 'inputSchema': {'type': 'object',
			'properties': {'session_id': {'type': 'string', 'description': 'Session name (as shown by list_sessions).'}},
			'required': ['session_id']},
		 'annotations': {'title': 'Get session info', 'readOnlyHint': True, 'openWorldHint': True}},
		{'name': 'exec_in_session',
		 'description': 'Run a shell command in a session and return its output. WARNING: arbitrary command execution on the target.',
		 'inputSchema': {'type': 'object',
			'properties': {'session_id': {'type': 'string', 'description': 'Session name (as shown by list_sessions).'},
				'command': {'type': 'string', 'description': 'Shell command to run on the target.'}},
			'required': ['session_id', 'command']},
		 'annotations': {'title': 'Exec in session', 'readOnlyHint': False,
			'destructiveHint': True, 'openWorldHint': True}},
		{'name': 'kill_session',
		 'description': 'Kill (close) a session. Returns once the kill is scheduled.',
		 'inputSchema': {'type': 'object',
			'properties': {'session_id': {'type': 'string', 'description': 'Session name (as shown by list_sessions).'}},
			'required': ['session_id']},
		 'annotations': {'title': 'Kill session', 'readOnlyHint': False,
			'destructiveHint': True, 'idempotentHint': True, 'openWorldHint': True}},
		{'name': 'upload_to_session',
		 'description': 'Upload local file(s)/URL(s) to a session. local_path supports globs (shlex). remote_path defaults to session cwd.',
		 'inputSchema': {'type': 'object',
			'properties': {'session_id': {'type': 'string', 'description': 'Session name (as shown by list_sessions).'},
				'local_path': {'type': 'string', 'description': 'Local file path(s) or URL(s).'},
				'remote_path': {'type': 'string', 'description': 'Remote directory (optional).'}},
			'required': ['session_id', 'local_path']},
		 'annotations': {'title': 'Upload to session', 'readOnlyHint': False,
			'destructiveHint': True, 'openWorldHint': True}},
		{'name': 'download_from_session',
		 'description': 'Download remote file(s) from a session (globs ok). Saved to the session downloads folder.',
		 'inputSchema': {'type': 'object',
			'properties': {'session_id': {'type': 'string', 'description': 'Session name (as shown by list_sessions).'},
				'remote_path': {'type': 'string', 'description': 'Remote file path(s) or glob(s).'}},
			'required': ['session_id', 'remote_path']},
		 'annotations': {'title': 'Download from session', 'readOnlyHint': False, 'openWorldHint': True}},
	]

	def __init__(self, host='127.0.0.1', port=0, token=None, certfile=None, keyfile=None):
		self.host = host
		self.port = port
		self.token = token or secrets.token_urlsafe(32)
		if bool(certfile) != bool(keyfile):
			raise ValueError('Both certfile and keyfile are required for MCP TLS')
		self.certfile = certfile
		self.keyfile = keyfile

	@staticmethod
	def _content_length(value):
		"""Return a validated HTTP body length, raising ValueError otherwise."""
		if value is None or not value.strip():
			raise ValueError('Content-Length is required')
		value = value.strip()
		if not value.isdigit():
			raise ValueError('Invalid Content-Length')
		return int(value)

	@classmethod
	def _truncate_output(cls, value):
		"""Limit UTF-8 output bytes without returning a broken character."""
		encoded = value.encode('utf-8')
		if len(encoded) <= cls.MAX_OUTPUT:
			return value
		marker = f"\n...[truncated, {len(encoded)} bytes total]"
		limit = cls.MAX_OUTPUT - len(marker.encode('utf-8'))
		if limit < 0:
			return marker.encode('utf-8')[:cls.MAX_OUTPUT].decode('utf-8', errors='ignore')
		return encoded[:limit].decode('utf-8', errors='ignore') + marker

	@classmethod
	def _bound_response(cls, payload):
		"""Prevent oversized batch/static responses from amplifying requests."""
		if len(payload) <= cls.MAX_RESPONSE:
			return payload
		return (b'{"jsonrpc":"2.0","id":null,"error":'
			b'{"code":-32603,"message":"Response too large"}}')

	@staticmethod
	def config_path():
		return options.basedir / 'mcp.json'

	@classmethod
	def load_config(cls):
		"""Load persisted host/port/token, or {} if none/unreadable."""
		try:
			with open(cls.config_path()) as f:
				cfg = json.load(f)
			return cfg if isinstance(cfg, dict) else {}
		except (OSError, ValueError):
			return {}

	def save_config(self):
		"""Persist host/port/token (owner-only, created 0600)."""
		path = self.config_path()
		try:
			path.parent.mkdir(parents=True, exist_ok=True)
			fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
			os.fchmod(fd, 0o600)
			with os.fdopen(fd, 'w') as f:
				json.dump({'host': self.host, 'port': self.port, 'token': self.token}, f)
		except OSError as e:
			logger.warning(f"Could not save MCP config to {path}: {e}")
		return self

	@staticmethod
	def _require_session(args):
		"""Validate session_id and return the live Session, or raise ValueError."""
		sid = args.get('session_id')
		if not isinstance(sid, str) or not sid:
			raise ValueError('session_id must be a non-empty string')
		s = core.sessions.get(sid)
		if s is None:
			raise ValueError(f'session {sid} not found')
		return s

	def _tool_call(self, name, args):
		"""Execute one MCP tool against live sessions. Raises ValueError on bad input."""
		if name == 'list_sessions':
			return {'sessions': [{'id': s.id, 'name': s.name, 'ip': s.ip, 'port': s.port,
				'OS': s.OS, 'type': s.type, 'subtype': s.subtype, 'user': s.user, 'source': s.source}
				for s in list(core.sessions.values())]}

		if name == 'get_session_info':
			s = self._require_session(args)
			return {'id': s.id, 'name': s.name, 'ip': s.ip, 'port': s.port, 'OS': s.OS,
				'type': s.type, 'subtype': s.subtype, 'user': s.user, 'source': s.source,
				'hostname': s.hostname, 'system': s.system, 'arch': s.arch, 'cwd': s.cwd}

		if name == 'exec_in_session':
			s = self._require_session(args)
			cmd = (args.get('command') or '').strip()
			if not cmd:
				raise ValueError('command is required')
			max_cmd = Messenger.MAX_PAYLOAD - 1 - 3 * Messenger.STREAM_BYTES
			cmd_len = len(cmd.encode())
			if cmd_len > max_cmd:
				raise ValueError(f'command too long ({cmd_len} bytes, max {max_cmd})')
			result = s.exec(cmd, value=True)
			if result is False or result is None:
				return {'error': 'exec failed or session not ready'}
			return {'output': result}

		if name == 'kill_session':
			s = self._require_session(args)
			s.kill()
			return {'ok': True}

		if name == 'upload_to_session':
			s = self._require_session(args)
			local_path = (args.get('local_path') or '').strip()
			if not local_path:
				raise ValueError('local_path is required')
			if not s.agent and not s.has_persistent_shell:
				return {'error': 'this shell runs each command in a fresh process; '
					'file transfer needs a persistent shell. Run spawn first.'}
			uploaded = s.upload(local_path, remote_path=args.get('remote_path') or None)
			return {'uploaded': uploaded}

		if name == 'download_from_session':
			s = self._require_session(args)
			remote = (args.get('remote_path') or '').strip()
			if not remote:
				raise ValueError('remote_path is required')
			if not s.agent and not s.has_persistent_shell:
				return {'error': 'this shell runs each command in a fresh process; '
					'file transfer needs a persistent shell. Run spawn first.'}
			return {'downloaded': [str(p) for p in s.download(remote)]}

		raise ValueError(f'unknown tool: {name}')

	def _jsonrpc(self, req):
		"""Handle one JSON-RPC 2.0 message. Returns a response dict, or None for notifications."""
		if not isinstance(req, dict):
			return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid Request'}}
		req_id = req.get('id')
		method = req.get('method', '')
		params = req.get('params') or {}

		if method == 'initialize':
			want = params.get('protocolVersion')
			version = want if want in self.PROTOCOL_VERSIONS else self.PROTOCOL_VERSIONS[0]
			return {'jsonrpc': '2.0', 'id': req_id, 'result': {
				'protocolVersion': version,
				'capabilities': {'tools': {}},
				'serverInfo': {'name': 'ariadne', 'version': '1.0.0'}}}

		if method == 'tools/list':
			return {'jsonrpc': '2.0', 'id': req_id, 'result': {'tools': self.TOOLS}}

		if method == 'tools/call':
			try:
				result = self._tool_call(params.get('name'), params.get('arguments') or {})
			except ValueError as e:
				return {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32602, 'message': str(e)}}
			except Exception as e:
				logger.exception("MCP tool error")
				return {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32603, 'message': f'Internal error: {e}'}}
			text = f"Error: {result['error']}" if 'error' in result else json.dumps(result, indent=2)
			text = self._truncate_output(text)
			return {'jsonrpc': '2.0', 'id': req_id,
				'result': {'content': [{'type': 'text', 'text': text}], 'isError': 'error' in result}}

		if method.startswith('notifications/'):
			return None

		if req_id is not None:
			return {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32601, 'message': f'method not found: {method}'}}
		return None

	def start(self):
		"""Bind and serve in a background thread; resolves self.port if it was 0. Returns self."""
		import hmac
		from http.server import BaseHTTPRequestHandler
		try:
			from http.server import ThreadingHTTPServer
		except ImportError:  # Python 3.6 lacks ThreadingHTTPServer
			import socketserver
			from http.server import HTTPServer
			class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
				daemon_threads = True
				block_on_close = False
		mcp = self

		class Handler(BaseHTTPRequestHandler):
			protocol_version = 'HTTP/1.1'

			def log_message(self, *a):
				pass

			def _reply(self, code, payload=b'', ctype='application/json'):
				self.send_response(code)
				self.send_header('Content-Length', str(len(payload)))
				if payload:
					self.send_header('Content-Type', ctype)
				self.end_headers()
				if payload:
					self.wfile.write(payload)

			def _authorized(self):
				origin = self.headers.get('Origin')
				if origin and not re.match(r'^https?://(127\.0\.0\.1|localhost)(:\d+)?$', origin):
					return False
				auth = self.headers.get('Authorization', '')
				prefix = 'Bearer '
				return auth.startswith(prefix) and hmac.compare_digest(auth[len(prefix):], mcp.token)

			def do_GET(self):
				self._reply(405)

			def do_POST(self):
				if not self._authorized():
					return self._reply(401)
				try:
					length = mcp._content_length(self.headers.get('Content-Length'))
				except ValueError:
					return self._reply(400)
				if length > mcp.MAX_REQUEST:
					return self._reply(400)
				try:
					body = self.rfile.read(length)
					if len(body) != length:
						return self._reply(400)
					req = json.loads(body or b'{}')
				except (json.JSONDecodeError, UnicodeDecodeError):
					return self._reply(200, json.dumps(
						{'jsonrpc': '2.0', 'id': None,
						 'error': {'code': -32700, 'message': 'Parse error'}}).encode())
				if isinstance(req, list):
					out = [r for r in (mcp._jsonrpc(x) for x in req) if r is not None]
					payload = json.dumps(out).encode() if out else b''
				else:
					resp = mcp._jsonrpc(req)
					payload = json.dumps(resp).encode() if resp is not None else b''
				payload = mcp._bound_response(payload)
				self._reply(200 if payload else 202, payload)

		class _Server(ThreadingHTTPServer):
			daemon_threads = True
			def handle_error(self, request, client_address):
				if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
					super().handle_error(request, client_address)

		srv = _Server((self.host, self.port), Handler)
		if self.certfile:
			context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
			context.load_cert_chain(self.certfile, self.keyfile)
			srv.socket = context.wrap_socket(srv.socket, server_side=True)
		self.port = srv.server_address[1]
		threading.Thread(target=srv.serve_forever, daemon=True, name='MCP').start()

		if self.host not in ('127.0.0.1', 'localhost', '::1') and not self.host.startswith('127.'):
			logger.warning(f"MCP is bound to {self.host} (non-loopback) and is reachable over the network. "
			               f"Prefer 127.0.0.1 and forward it instead: ssh -L {self.port}:127.0.0.1:{self.port} <host>")

		url = f"{'https' if self.certfile else 'http'}://{self.host}:{self.port}/"
		logger.info(f"MCP server listening on {url}. Register with:")
		print(f'claude mcp add --transport http ariadne {url} --header "Authorization: Bearer {self.token}"')
		return self
