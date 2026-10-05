#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *
from .options import options
from .log import logger, cmdlogger
from .display import paint, Table, TAGS, Interfaces
from .network import handle_bind_errors

# core: injected by ariadne/__init__.py after the singleton exists.

class FileServer:
	def __init__(self, *items, port=None, host=None, url_prefix=None, quiet=False, upload=False, upload_dir=None):
		self.port = options.default_fileserver_port if port is None else port
		self.host = host or options.default_interface
		self.host = Interfaces().translate(self.host)
		self.items = items
		self.url_prefix = url_prefix + '/' if url_prefix else ''
		self.quiet = quiet
		self.upload = upload
		self.upload_dir = os.path.abspath(normalize_path(upload_dir)) if upload_dir else os.getcwd()
		self.init = threading.Event()
		self.term = threading.Event()
		self.filemap = {}
		for item in self.items:
			self.add(item)

	def add(self, item):
		if item == '/':
			self.filemap[f'/{self.url_prefix}[root]'] = '/'
			return '/[root]'

		item = os.path.abspath(normalize_path(item))

		if not os.path.exists(item):
			if not self.quiet:
				logger.warning(f"'{item}' does not exist and will be ignored.")
			return None

		if item in self.filemap.values():
			for _urlpath, _item in self.filemap.items():
				if _item == item:
					return _urlpath

		urlpath = f"/{self.url_prefix}{os.path.basename(item)}"
		while urlpath in self.filemap:
			root, ext = os.path.splitext(urlpath)
			urlpath = root + '_' + ext
		self.filemap[urlpath] = item
		return urlpath

	def remove(self, item):
		item = os.path.abspath(normalize_path(item))
		for urlpath, filepath in self.filemap.items():
			if filepath == item:
				del self.filemap[urlpath]
				return
		if not self.quiet:
			logger.warning(f"{item} is not served.")

	@property
	def links(self):
		output = []
		ips = [self.host]

		if self.host == '0.0.0.0':
			ips = Interfaces().ips

		for ip in ips:
			output.extend(('', 'http://' + str(paint(ip).cyan) + ":" + str(paint(self.port).orange) + '/' + self.url_prefix))
			if self.upload:
				url = f"http://{ip}:{self.port}/{self.url_prefix}"
				linux_cmd = f" curl {url} -T <filepath>"
				win_cmd = f"(New-Object Net.WebClient).UploadFile('{url}','POST','<filepath>')"
				output.append(f'Upload enabled -> {paint(self.upload_dir).green}')
				output.append(f"     Linux   : {linux_cmd}")
				output.append(f"     Windows : {win_cmd}")
			table = Table(joinchar=' -> ')
			for urlpath, filepath in self.filemap.items():
				table += (
					paint(f"{TAGS['folder'] if os.path.isdir(filepath) else TAGS['file']} ").green +
					paint(f"http://{ip}:{self.port}{urlpath}").white_BLUE, filepath
				)
			table_str = str(table)
			if table_str:
				output.append(table_str)
			output.append("─" * len(output[1]))

		return '\n'.join(output)

	def start(self):
		threading.Thread(target=self._start).start()

	def _start(self):
		filemap, host, port, url_prefix, quiet = self.filemap, self.host, self.port, self.url_prefix, self.quiet
		upload, upload_dir = self.upload, self.upload_dir

		class CustomTCPServer(socketserver.ThreadingTCPServer):
			allow_reuse_address = True
			daemon_threads = True
			block_on_close = False

			def __init__(self, *args, **kwargs):
				self.client_sockets = set()
				super().__init__(*args, **kwargs)

			@handle_bind_errors
			def server_bind(self, host, port):
				self.server_address = (host, int(port))
				super().server_bind()

			def process_request(self, request, client_address):
				self.client_sockets.add(request)
				super().process_request(request, client_address)

			def shutdown_request(self, request):
				self.client_sockets.discard(request)
				super().shutdown_request(request)

			def shutdown(self):
				for sock in list(self.client_sockets):
					try:
						sock.shutdown(socket.SHUT_RDWR)
						sock.close()
					except OSError:
						pass
				super().shutdown()

		from http.server import SimpleHTTPRequestHandler
		class CustomHandler(SimpleHTTPRequestHandler):
			def do_GET(self):
				try:
					if self.path == '/' + url_prefix:
						from html import escape
						response = ''
						if upload:
							response += (
								'<form method="POST" enctype="multipart/form-data">'
								'<input type="file" name="file" multiple>'
								'<input type="submit" value="Upload"></form><hr>'
							)
						for path in list(filemap.keys()):
							safe_path = escape(path)
							response += f'<li><a href="{safe_path}">{safe_path}</a></li>'
						response = response.encode()
						self.send_response(200)
						self.send_header("Content-type", "text/html")
						self.send_header("Content-Length", str(len(response)))
						self.end_headers()

						self.wfile.write(response)
					else:
						super().do_GET()
				except Exception as e:
					logger.error(e)

			def _save_upload(self, filename, data):
				filename = os.path.basename((filename or '').replace('\\', '/')).lstrip('.') or f"upload_{int(time.time())}"
				os.makedirs(upload_dir, exist_ok=True)
				base, ext = os.path.splitext(os.path.join(upload_dir, filename))
				dest = base + ext
				while True:
					try:
						fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
						break
					except FileExistsError:
						base += '_'
						dest = base + ext
				with os.fdopen(fd, 'wb') as f:
					f.write(data or b'')
				if not quiet:
					logger.info(
						f"{paint('[').white}{paint(self.log_date_time_string()).magenta}] "
						f"FileServer({host}:{port}) [{paint(self.address_string()).cyan}] "
						f"{paint('UPLOAD').yellow} -> {paint(dest).green} "
						f"({paint(str(len(data or b''))).orange} bytes)"
					)
				return dest

			def _read_body(self):
				length = int(self.headers.get('Content-Length', 0) or 0)
				if length < 0:
					raise ValueError(f"Negative Content-Length ({length})")
				if not length:
					return b''
				data = self.rfile.read(length)
				if len(data) != length:
					raise ValueError(f"Truncated upload: {len(data)} of {length} bytes")
				return data

			def _reject_upload(self, method):
				self.send_error(501, f"Unsupported method ('{method}')")

			def do_PUT(self):
				if not upload:
					return self._reject_upload('PUT')
				try:
					self._save_upload(unquote(self.path), self._read_body())
					self.send_response(201)
					self.send_header("Content-Length", "0")
					self.end_headers()
				except Exception as e:
					logger.error(e)
					self.send_error(500)

			def do_POST(self):
				if not upload:
					return self._reject_upload('POST')
				try:
					ctype = self.headers.get('Content-Type', '')
					body = self._read_body()
					saved = []
					if ctype.startswith('multipart/form-data'):
						import email
						msg = email.message_from_bytes(
							b'Content-Type: ' + ctype.encode() + b'\r\nMIME-Version: 1.0\r\n\r\n' + body
						)
						for part in msg.walk():
							fname = part.get_filename()
							if fname:
								saved.append(self._save_upload(fname, part.get_payload(decode=True)))
					else:
						saved.append(self._save_upload(unquote(self.path), body))

					if ctype.startswith('multipart/form-data'):
						self.send_response(303)
						self.send_header("Location", '/' + url_prefix)
						self.send_header("Content-Length", "0")
						self.end_headers()
					else:
						msg = ('\n'.join(os.path.basename(p) for p in saved) + '\n').encode()
						self.send_response(201)
						self.send_header("Content-Length", str(len(msg)))
						self.end_headers()
						self.wfile.write(msg)
				except Exception as e:
					logger.error(e)
					self.send_error(500)

			def translate_path(self, path):
				path = path.split('?', 1)[0]
				path = path.split('#', 1)[0]
				try:
					path = unquote(path, errors='surrogatepass')
				except UnicodeDecodeError:
					path = unquote(path)
				path = os.path.normpath(path)

				for urlpath, filepath in list(filemap.items()):
					if path == urlpath:
						return filepath
					elif path.startswith(urlpath + '/'):
						relpath = path[len(urlpath):].lstrip('/')
						return os.path.join(filepath, relpath)
				return ""

			def log_message(self, format, *args):
				if quiet:
					return None
				message = format % args
				control_char_table = getattr(self, '_control_char_table', HTTP_CONTROL_CHAR_TABLE)
				response = message.translate(control_char_table).split(' ')
				if len(response) < 4 or not response[0].startswith('"'):
					return
				if response[3][0] == '3':
					color = 'yellow'
				elif response[3][0] in ('4', '5'):
					color = 'red'
				else:
					color = 'green'

				response = getattr(paint(f"{response[0]} {response[1]} {response[3]}\""), color)

				logger.info(
					f"{paint('[').white}{paint(self.log_date_time_string()).magenta}] "
					f"FileServer({host}:{port}) [{paint(self.address_string()).cyan}] {response}"
				)

		with CustomTCPServer((self.host, self.port), CustomHandler, bind_and_activate=False) as self.httpd:
			if not self.httpd.server_bind(self.host, self.port):
				self.init.set()
				return False
			self.port = self.httpd.server_address[1]
			self.httpd.server_activate()
			self.id = core.new_fileserverID
			core.fileservers[self.id] = self
			if not quiet:
				print(self.links)
			self.init.set()
			self.httpd.serve_forever()

	def stop(self):
		if hasattr(self, 'id'):
			del core.fileservers[self.id]
			if not self.quiet:
				logger.warning(f"Shutting down Fileserver #{self.id}")
			self.httpd.shutdown()
		self.term.set()
