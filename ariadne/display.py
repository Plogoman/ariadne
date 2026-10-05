#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *

# core, logger, options: injected by ariadne/__init__.py after the
# singletons exist (options is injected here rather than imported at top
# level to avoid a circular import: options.py itself imports paint from
# this module).

def Open(item, terminal=False):
	if myOS != 'Darwin' and not DISPLAY:
		logger.error("No available $DISPLAY")
		return False

	if not terminal:
		program = 'xdg-open' if myOS != 'Darwin' else 'open'
		args = [item]
	elif myOS == 'Darwin':
		try:
			fd, cmd_path = tempfile.mkstemp(prefix='ariadne-', suffix='.command')
			with os.fdopen(fd, 'w') as f:
				f.write(f"#!/bin/sh\n{item}\n")
			os.chmod(cmd_path, 0o700)
		except OSError as e:
			logger.error(f"Cannot open terminal window: {e}")
			return False
		program, args = 'open', [cmd_path]
	else:
		program = terminal_emulator()
		if not program:
			logger.error("No available terminal emulator")
			return False
		if program == 'xdg-terminal-exec':
			args = [*shlex.split(item)]
		else:
			_switch = '-e'
			if program in ('gnome-terminal', 'mate-terminal'):
				_switch = '--'
			elif program == 'terminator':
				_switch = '-x'
			elif program == 'xfce4-terminal':
				_switch = '--command='
			args = [_switch, *shlex.split(item)]

	if not shutil.which(program):
		logger.error(f"Cannot open window: '{program}' binary does not exist")
		return False

	with tempfile.TemporaryFile() as stderr_file:
		process = subprocess.Popen(
			(program, *args),
			stdin=subprocess.DEVNULL,
			stdout=subprocess.DEVNULL,
			stderr=stderr_file
		)

		try:
			process.wait(timeout=.01 if terminal else 2)
		except subprocess.TimeoutExpired:
			if terminal:
				stderr_file.seek(0)
				error = stderr_file.read(1024)
				if error:
					logger.error(error.decode(errors="replace"))
					return False
			return True
		if process.returncode != 0:
			stderr_file.seek(0)
			error = stderr_file.read().decode(errors="replace").strip()
			logger.error(f"Could not open '{item}'" + (f": {error}" if error else f" (exit code {process.returncode})"))
			return False
		return True




class Interfaces:
	def __str__(self):
		table = Table(joinchar=' : ')
		table.header = [paint('Interface').MAGENTA, paint('IP Address').MAGENTA]
		grouped = {}
		for name, ip in self.pairs:
			grouped.setdefault(name, []).append(ip)
		for name, ips in grouped.items():
			table += [paint(name).cyan, paint(', '.join(ips)).yellow]
		return str(table)

	def translate(self, interface_name):
		interfaces = self.list
		if interface_name in interfaces:
			return interfaces[interface_name]
		elif interface_name in ('any', 'all'):
			return '0.0.0.0'
		else:
			return interface_name

	@staticmethod
	def ipa(busybox=False):
		interfaces = []
		current_interface = None
		params = ['ip', 'addr']
		if busybox:
			params.insert(0, 'busybox')
		try:
			output = subprocess.check_output(params, stderr=subprocess.DEVNULL).decode(errors="replace")
		except (subprocess.CalledProcessError, OSError):
			return interfaces
		for line in output.splitlines():
			interface = re.search(r"^\d+:\s+(.+?)(?:@\w+)?:", line)
			if interface:
				current_interface = interface[1]
				continue
			if current_interface:
				ip = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)", line)
				if ip:
					interfaces.append((current_interface, ip[1]))
		return interfaces

	@staticmethod
	def ifconfig():
		interfaces = []
		try:
			output = subprocess.check_output(['ifconfig'], stderr=subprocess.DEVNULL).decode(errors="replace")
		except (subprocess.CalledProcessError, OSError):
			return interfaces
		current_interface = None
		for line in output.splitlines():
			if line and not line[0].isspace():
				header = re.match(r'(\S+?):?\s', line)
				current_interface = header[1] if header else None
			elif current_interface:
				ip = re.search(r'\binet (?:addr:)?(\d+\.\d+\.\d+\.\d+)', line)
				if ip:
					interfaces.append((current_interface, ip[1]))
		return interfaces

	@property
	def pairs(self):
		if shutil.which("ip"):
			return self.ipa()
		elif shutil.which("ifconfig"):
			return self.ifconfig()
		elif shutil.which("busybox"):
			return self.ipa(busybox=True)
		logger.error("'ip', 'ifconfig' and 'busybox' commands are not available. (Really???)")
		return []

	@property
	def list(self):
		result = {}
		for name, ip in self.pairs:
			result.setdefault(name, ip)
		return result

	@property
	def ips(self):
		seen = set()
		result = []
		for _, ip in self.pairs:
			if ip not in seen:
				seen.add(ip)
				result.append(ip)
		return result

	@property
	def list_all(self):
		seen = set()
		result = []
		for name, ip in self.pairs:
			for item in (name, ip):
				if item not in seen:
					seen.add(item)
					result.append(item)
		return result




class Table:

	def __init__(self, list_of_lists=None, header=None, fillchar=" ", joinchar=" "):
		self.list_of_lists = [] if list_of_lists is None else list_of_lists
		self.joinchar = joinchar

		if type(fillchar) is str:
			self.fillchar = [fillchar]
		elif type(fillchar) is list:
			self.fillchar = fillchar
#		self.fillchar[0] = self.fillchar[0][0]

		self.data = []
		self.max_row_len = 0
		self.col_max_lens = []
		if header: self.header = header
		for row in self.list_of_lists:
			self += row

	@property
	def header(self):
		...

	@header.setter
	def header(self, header):
		self.add_row(header, header=True)

	def __str__(self):
		self.fill()
		return "\n".join([self.joinchar.join(row) for row in self.data])

	def __len__(self):
		return len(self.data)

	def add_row(self, row, header=False):
		row_len = len(row)
		if row_len > self.max_row_len:
			self.max_row_len = row_len

		cur_col_len = len(self.col_max_lens)
		for _ in range(row_len - cur_col_len):
			self.col_max_lens.append(0)

		for _ in range(cur_col_len - row_len):
			row.append("")

		new_row = []
		for index, element in enumerate(row):
			if not isinstance(element, (str, paint)):
				element = str(element)
			elem_length = len(element)
			new_row.append(element)
			if elem_length > self.col_max_lens[index]:
				self.col_max_lens[index] = elem_length

		if header:
			self.data.insert(0, new_row)
		else:
			self.data.append(new_row)

	def __iadd__(self, row):
		self.add_row(row)
		return self

	def fill(self):
		for row in self.data:
			for index, element in enumerate(row):
				fillchar = ' '
				if index in [*self.fillchar][1:]:
					fillchar = self.fillchar[0]
				row[index] = element + fillchar * (self.col_max_lens[index] - len(element))




class Size:
	units = ("", "K", "M", "G", "T", "P", "E", "Z", "Y")
	def __init__(self, _bytes):
		self.bytes = _bytes

	def __str__(self):
		index = 0
		new_size = self.bytes
		while new_size >= 1024 and index < len(__class__.units) - 1:
			new_size /= 1024
			index += 1
		return f"{new_size:.1f} {__class__.units[index]}B"

	@classmethod
	def from_str(cls, string):
		if string.isnumeric():
			_bytes = int(string)
		else:
			try:
				num, unit = int(string[:-1]), string[-1]
				_bytes = num * 1024 ** __class__.units.index(unit)
			except ValueError:
				raise ValueError(f"Invalid size specified: {string!r}")
		return cls(_bytes)


from datetime import timedelta
from threading import Thread, RLock, current_thread


class PBar:
	pbars = []

	def __init__(self, end, caption="", barlen=None, queue=None, metric=None, reverse=False, clear=False):
		self.clear = clear
		self.end = end
		if type(self.end) is not int: self.end = len(self.end)
		self.active = True if self.end > 0 else False
		self.pos = 0
		self.percent = 0
		self.caption = caption
		self.bar = '•'
		self.reverse = reverse
		self.barlen = barlen
		self.percent_prev = -1
		self.queue = queue
		self.metric = metric
		self.check_interval = 1
		if self.queue:
			# Daemon: trace() only renders progress and exits on the None
			# posted by terminate(); if an error path skips terminate(), a
			# non-daemon waiter here would block _thread._shutdown() forever.
			self.trace_thread = Thread(target=self.trace, daemon=True); self.trace_thread.start(); __class__.render_lock = RLock()
		if self.metric: Thread(target=self.watch_speed, daemon=True).start()
		else: self.metric = lambda x: f"{x:,}"
		__class__.pbars.append(self)
		print("\x1b[?25l", end='', flush=True)
		self.render()

	def __bool__(self):
		return self.active

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc_value, traceback):
		self.terminate()

	def trace(self):
		while True:
			data = self.queue.get()
			self.queue.task_done()
			if isinstance(data, int): self.update(data)
			elif data is None: break
			else: self.print(data)

	def watch_speed(self):
		self.pos_prev = 0
		self.elapsed = 0
		started = previous = time.monotonic()
		while self:
			time.sleep(self.check_interval)
			now = time.monotonic()
			interval = now - previous
			self.elapsed = now - started
			self.speed = (self.pos - self.pos_prev) / interval if interval else 0
			self.pos_prev = self.pos
			previous = now
			self.speed_avg = self.pos / self.elapsed
			if self.speed_avg: self.eta = max(0, int((self.end - self.pos) / self.speed_avg))
			if self: self.render()

	def update(self, step=1):
		if not self: return False
		self.pos += step
		if self.pos >= self.end: self.pos = self.end
		self.percent = int(self.pos * 100 / self.end)
		if self.pos >= self.end: self.terminate()
		if self.percent > self.percent_prev: self.render()

	def render_one(self):
		self.percent_prev = self.percent
		left = f"{self.caption}"
		elapsed = "" if not hasattr(self, 'elapsed') else f" | Elapsed {timedelta(seconds=int(self.elapsed))}"
		speed = "" if not hasattr(self, 'speed') else f" | {self.metric(self.speed)}/s"
		eta = "" if not hasattr(self, 'eta') else f" | ETA {timedelta(seconds=self.eta)}"
		info = f"{str(self.percent).rjust(3)}% ({self.metric(self.pos)}/{self.metric(self.end)}){speed}{elapsed}{eta}"
		right = f" {paint(info).darkgrey}"
		try:
			columns = os.get_terminal_size().columns
		except OSError:
			columns = 80
		if self.barlen:
			bar_space = min(self.barlen, max(1, columns - visible_len(left) - visible_len(right)))
		else:
			bar_space = columns - visible_len(left) - visible_len(right)
		n = int(self.percent * bar_space / 100)
		color = 'softgreen' if self.reverse else 'softorange'
		fill  = f"{getattr(paint(self.bar * n), color)}"
		track = f"{paint('◦' * (bar_space - n)).darkgrey}"
		filled = track + fill if self.reverse else fill + track
		print(f'\x1b[2K{left}{filled}{right}\n', end='', flush=True)

	def render(self):
		if hasattr(__class__, 'render_lock'): __class__.render_lock.acquire()
		for pbar in __class__.pbars: pbar.render_one()
		print(f"\x1b[{len(__class__.pbars)}A", end='', flush=True)
		if hasattr(__class__, 'render_lock'): __class__.render_lock.release()

	def print(self, data):
		if hasattr(__class__, 'render_lock'): __class__.render_lock.acquire()
		print(f"\x1b[2K{data}", flush=True)
		self.render()
		if hasattr(__class__, 'render_lock'): __class__.render_lock.release()

	def terminate(self):
		if self.queue and current_thread() != self.trace_thread: self.queue.join(); self.queue.put(None)
		if hasattr(__class__, 'render_lock'): __class__.render_lock.acquire()
		try:
			if not self: return
			self.active = False
			if hasattr(self, 'eta'): del self.eta
			if not any(__class__.pbars):
				n = len(__class__.pbars)
				if self.clear:
					print("\x1b[?25h" + ("\x1b[2K\x1b[1B" * n) + f"\x1b[{n}A", end='', flush=True)
				else:
					self.render()
					print("\x1b[?25h" + '\n' * n, end='', flush=True)
				__class__.pbars.clear()
		finally:
			if hasattr(__class__, 'render_lock'): __class__.render_lock.release()




class paint:
	_codes = {'RESET':0, 'BRIGHT':1, 'DIM':2, 'UNDERLINE':4, 'BLINK':5, 'NORMAL':22}
	_colors = {'black':0, 'red':1, 'green':2, 'yellow':3, 'blue':4, 'magenta':5, 'cyan':6, 'orange':208, 'white':15, 'lightgrey':250, 'darkgrey':242, 'softorange':173, 'softgreen':71}
	_escape = lambda codes: f"\001\x1b[{codes}m\002"

	def __init__(self, text=None, colors=None):
		self.text = str(text) if text is not None else None
		self.colors = colors or []

	def __str__(self):
		if self.colors:
			content = self.text + __class__._escape(__class__._codes['RESET']) if self.text is not None else ''
			return __class__._escape(';'.join(self.colors)) + content
		return self.text

	def __len__(self):
		return visible_len(self.text) if self.text else 0

	def __add__(self, text):
		return str(self) + str(text)

	def __mul__(self, num):
		return __class__(self.text * num, self.colors)

	def __getattr__(self, attr):
		self.colors.clear()
		for color in attr.split('_'):
			if color in __class__._codes:
				self.colors.append(str(__class__._codes[color]))
			else:
				prefix = "3" if color in __class__._colors else "4"
				self.colors.append(prefix + "8;5;" + str(__class__._colors[color.lower()]))
		return self




class CustomFormatter(logging.Formatter):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.templates = {
			logging.CRITICAL: {'color':"RED",     'prefix':"[!!!]"},
			logging.ERROR:    {'color':"red",     'prefix':"[-]"},
			logging.WARNING:  {'color':"yellow",  'prefix':"[!]"},
			logging.TRACE:    {'color':"cyan",    'prefix':"[•]"},
			logging.INFO:     {'color':"green",   'prefix':"[+]"},
			logging.DEBUG:    {'color':"magenta", 'prefix':"[DEBUG]"}
		}

	def format(self, record):
		template = self.templates[record.levelno]

		thread = ""
		if record.levelno is logging.DEBUG or options.debug:
			thread = paint(" ") + paint(threading.current_thread().name).white_CYAN

		prefix = "\x1b[2K\r"
		suffix = "\r\n"

		if core.wait_input:
			suffix += bytes(core.output_line_buffer).decode(errors="replace") + (readline.get_line_buffer() if readline else '')

		elif core.attached_session:
			suffix += bytes(core.output_line_buffer).decode(errors="replace")

		text = f"{template['prefix']}{thread} {logging.Formatter.format(self, record)}"
		return f"{prefix}{getattr(paint(text), template['color'])}{suffix}"




class HelpFormatter(RawTextHelpFormatter):
	def _format_action_invocation(self, action):
		if not action.option_strings or action.nargs == 0:
			return super()._format_action_invocation(action)
		return ', '.join(action.option_strings)




class LineBuffer:
	def __init__(self, maxlines):
		self.lines = deque(maxlen=maxlines)
		self.lock = threading.Lock()

	def __lshift__(self, data):
		if isinstance(data, str):
			data = data.encode()
		with self.lock:
			partial = self.lines.pop() if self.lines else b''
			self.lines.extend((partial + data).rsplit(b'\n', self.lines.maxlen))
		return self

	def __bytes__(self):
		with self.lock:
			return b'\n'.join(self.lines)



def signal_level(rtt_ms, loss=False, jitter_ms=0):
	if rtt_ms is None:
		return -1
	lvl = 4 if rtt_ms < 30 else 3 if rtt_ms < 80 else 2 if rtt_ms < 150 else 1
	unstable = loss or (jitter_ms or 0) > 30
	return max(1, lvl - (1 if unstable else 0))



def signal_bars(level):
	glyphs = "▁▃▅▇"
	if level < 0:
		return str(paint(" ···").darkgrey)
	color = ("darkgrey", "red", "orange", "yellow", "green")[level]
	return "".join(
		str(getattr(paint(g), color)) if i < level else str(paint(g).darkgrey)
		for i, g in enumerate(glyphs)
	)

# --- bracketed paste filtering ---------------------------------------------
# Terminals with bracketed paste enabled wrap pasted text in
# \x1b[200~ ... \x1b[201~. Remote shells that do not understand these markers
# echo them as literal garbage (the classic "0~" prefix / "~1" suffix around
# pasted text), so strip them before forwarding stdin to a Raw session.
# PTY sessions are deliberately left alone: the remote turns bracketed paste
# on itself and relies on the markers arriving intact.
BRACKETED_PASTE_RE = re.compile(rb'\x1b\[20[01]~')
_BP_MARKER_PREFIXES = tuple(b'\x1b[201~'[:n] for n in range(5, 0, -1)) \
	+ tuple(b'\x1b[200~'[:n] for n in range(5, 0, -1))



TAGS = {
	'folder': '[DIR]',
	'file': '[FILE]',
}
