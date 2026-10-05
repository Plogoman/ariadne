#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *
from .options import options
from .log import logger, cmdlogger
from .display import paint, HelpFormatter, Table

# core: injected by ariadne/__init__.py after the singleton exists.

# The SIGINT handler in place before Ariadne's menu temporarily swaps it
# out during blocking input reads, restored afterwards.
keyboard_interrupt = signal.getsignal(signal.SIGINT)

try:
	import readline
	readline_basic_quote_chars = None
	if getattr(readline, 'backend', '') == 'editline' or 'libedit' in (readline.__doc__ or ''):
		readline.parse_and_bind("bind ^I rl_complete")
	else:
		readline.parse_and_bind("tab: complete")
		try:
			import ctypes
			_readline_empty_quote_chars = ctypes.create_string_buffer(b'')
			readline_basic_quote_chars = ctypes.c_void_p.in_dll(
				ctypes.CDLL(readline.__file__), 'rl_basic_quote_characters'
			)
		except (AttributeError, OSError, ValueError):
			pass
	default_readline_delims = readline.get_completer_delims()
except ImportError:
	readline = None
	readline_basic_quote_chars = None
	default_readline_delims = None

def stdout(data, record=True):
	try:
		os.write(sys.stdout.fileno(), data)
	except OSError:
		pass
	if record:
		core.output_line_buffer << data



def ask(text):
	while True:
		try:
			return input(f"{paint(f'[?] {text}').yellow}")
		except EOFError:
			print()
			if not sys.stdin.isatty():
				return ''
		except KeyboardInterrupt:
			print("^C")
			return ' '



def ask_listener():
	"""Pick which Listener the payloads should point at. Returns the chosen Listeners,
	or None if the question was not answered. Does not ask when there is only one."""
	listeners = list(core.listeners.values())
	if len(listeners) < 2:
		return listeners

	table = Table(joinchar=' | ')
	table.header = [paint(header).orange for header in ('ID', 'Host', 'Port')]
	for listener in listeners:
		table += [listener.id, listener.host, listener.port]
	print('\n', indent(str(table), '  '), '\n', sep='')

	while True:
		answer = ask("Listener ID? (<id>/*): ").strip()
		if not answer:
			return None
		if answer == '*':
			return listeners
		try:
			return [core.listeners[int(answer)]]
		except (KeyError, ValueError):
			logger.warning("Invalid Listener ID")



def ask_target_os():
	"""Ask which OS the target runs, so we only show payloads that can run there.
	Returns None if the question was not answered (empty input, Ctrl-C, or no tty)."""
	numbered = {'1': 'linux', '2': 'windows', '3': 'both'}
	while True:
		answer = ask("Target OS? (1/linux, 2/windows, 3/both): ").strip().lower()
		if not answer:
			return None
		if answer in numbered:
			return numbered[answer]
		for choice in ('linux', 'windows', 'both'):
			if choice.startswith(answer):
				return choice
		logger.warning("Answer 1/linux, 2/windows or 3/both")



def my_input(text="", histfile=None, histlen=None, completer=lambda text, state: None, completer_delims=None):
	readline_quote_chars_saved = None
	if threading.current_thread().name == 'MainThread':
		signal.signal(signal.SIGINT, keyboard_interrupt)

	if readline:
		if readline_basic_quote_chars is not None:
			readline_quote_chars_saved = readline_basic_quote_chars.value
			readline_basic_quote_chars.value = ctypes.addressof(_readline_empty_quote_chars)
		readline.set_completer(completer)
		readline.set_completer_delims(completer_delims or default_readline_delims)
		readline.clear_history()
		if histfile:
			try:
				readline.read_history_file(histfile)
			except Exception as e:
				cmdlogger.debug(f"Error loading history file: {e}")
		#readline.set_auto_history(True)

	core.output_line_buffer << b"\n" + text.encode()
	core.wait_input = True

	try:
		response = original_input(text)

		if readline:
			#readline.set_completer(None)
			#readline.set_completer_delims(default_readline_delims)
			if histfile:
				try:
					readline.set_history_length(options.histlength)
					#readline.add_history(response)
					readline.write_history_file(histfile)
				except Exception as e:
					cmdlogger.debug(f"Error writing to history file: {e}")
			#readline.set_auto_history(False)
		return response
	finally:
		if readline_quote_chars_saved is not None:
			readline_basic_quote_chars.value = readline_quote_chars_saved
		core.wait_input = False



class BetterCMD:
	def __init__(self, prompt=None, banner=None, histfile=None, histlen=None):
		self.prompt = prompt
		self.banner = banner
		self.histfile = histfile
		self.histlen = histlen
		self.cmdqueue = []
		self.lastcmd = ''
		self.active = threading.Event()
		self.stop = False

	def show(self):
		print()
		self.active.set()

	def start(self):
		self.preloop()
		if self.banner:
			print(self.banner)

		stop = None
		while not self.stop:
			try:
				try:
					self.active.wait()
					if self.cmdqueue:
						line = self.cmdqueue.pop(0)
					else:
						line = input(self.prompt, self.histfile, self.histlen, self.complete, " \t\n\"'><=;|&(")

					signal.signal(signal.SIGINT, lambda num, stack: self.interrupt())
					line = self.precmd(line)
					stop = self.onecmd(line)
					stop = self.postcmd(stop, line)
					if stop:
						self.active.clear()
				except EOFError:
					stop = self.onecmd('EOF')
				except Exception:
					custom_excepthook(*sys.exc_info())
			except KeyboardInterrupt:
				print("^C")
				self.interrupt()
		self.postloop()

	def onecmd(self, line):
		cmd, arg, line = self.parseline(line)
		if cmd:
			try:
				func = getattr(self, 'do_' + cmd)
				self.lastcmd = line
			except AttributeError:
				return self.default(line)
			return func(arg)

	def default(self, line):
		cmdlogger.error("Invalid command")

	def interrupt(self):
		pass

	def parseline(self, line):
		line = line.lstrip()
		if not line:
			return None, None, line
		elif line[0] == '!':
			if not readline:
				cmdlogger.error("Command history recall requires readline support")
				return None, None, line
			index = line[1:].strip()
			hist_len = readline.get_current_history_length()

			if not index.isnumeric() or not (0 < int(index) < hist_len):
				cmdlogger.error("Invalid command number")
				readline.remove_history_item(hist_len - 1)
				return None, None, line

			line = readline.get_history_item(int(index))
			readline.replace_history_item(hist_len - 1, line)
			return self.parseline(line)

		else:
			parts = line.split(' ', 1)
			if len(parts) == 1:
				return parts[0], None, line
			elif len(parts) == 2:
				return parts[0], parts[1], line

	def precmd(self, line):
		return line

	def postcmd(self, stop, line):
		return stop

	def preloop(self):
		pass

	def postloop(self):
		pass

	def do_reset(self, line):
		"""

		Reset the local terminal
		"""
		if shutil.which("reset"):
			os.system("reset")
		else:
			cmdlogger.error("'reset' command doesn't exist on the system")

	def do_exit(self, line):
		"""

		Exit cmd
		"""
		self.stop = True
		self.active.clear()

	def do_history(self, line):
		"""

		Show Main Menu history
		"""
		if readline:
			hist_len = readline.get_current_history_length()
			max_digits = len(str(hist_len))
			for i in range(1, hist_len + 1):
				print(f"  {i:>{max_digits}}  {readline.get_history_item(i)}")
		else:
			cmdlogger.error("Python is not compiled with readline support")

	def do_DEBUG(self, line):
		"""

		Open debug console
		"""
		import rlcompleter

		if readline:
			readline.clear_history()
			try:
				readline.read_history_file(options.debug_histfile)
			except Exception as e:
				cmdlogger.debug(f"Error loading history file: {e}")

		interact(banner=paint(
			"===> Entering debugging console...").CYAN, local=globals(),
			exitmsg=paint("<=== Leaving debugging console..."
		).CYAN)

		if readline:
			readline.set_history_length(options.histlength)
			try:
				readline.write_history_file(options.debug_histfile)
			except Exception as e:
				cmdlogger.debug(f"Error writing to history file: {e}")

	def completedefault(self, *ignored):
		return []

	def resolve_command(self, command):
		return command

	def completenames(self, text, *ignored):
		dotext = 'do_' + text
		return [a[3:] for a in dir(self.__class__) if a.startswith(dotext)]

	def complete(self, text, state):
		if state == 0:
			origline = readline.get_line_buffer()
			line = origline.lstrip()
			stripped = len(origline) - len(line)
			begidx = readline.get_begidx() - stripped
			endidx = readline.get_endidx() - stripped
			if begidx > 0:
				cmd, args, _line = self.parseline(line)
				if cmd == '':
					compfunc = self.completedefault
				else:
					cmd = self.resolve_command(cmd)
					try:
						compfunc = getattr(self, 'complete_' + cmd)
					except AttributeError:
						compfunc = self.completedefault
			else:
				compfunc = self.completenames
			self.completion_matches = compfunc(text, line, begidx, endidx)
			active_arg, i = line[:endidx], 0
			while i < len(active_arg):
				if active_arg[i] == '\\':
					i += 2
					continue
				if active_arg[i].isspace():
					active_arg = active_arg[i + 1:]
					i = 0
					continue
				i += 1
			if self.completion_matches == [text] and ("\\'" in active_arg or '\\"' in active_arg):
				self.completion_matches = []
		try:
			return self.completion_matches[state]
		except IndexError:
			return None

	@staticmethod
	def complete_path(line, begidx, endidx, lister, expand=lambda p: p, windows=False):
		if windows:
			arg_start, quoted, i = 0, False, 0
			while i < endidx:
				c = line[i]
				if c == '"':
					quoted = not quoted
					if quoted:
						arg_start = i + 1
				elif c == ' ' and not quoted:
					arg_start = i + 1
				i += 1
			pattern = expand(line[arg_start:endidx])
			cut = begidx - arg_start
			pat_ci = pattern.lower()
			results = []
			for m in lister(pattern):
				if not m.lower().startswith(pat_ci):
					continue
				is_dir = m.endswith(('\\', '/'))
				if quoted:
					rendered = m if is_dir else m + '"'
				elif ' ' in m:
					rendered = ('"' + m) if is_dir else ('"' + m + '"')
				else:
					rendered = m
				results.append(rendered[cut:])
			return results

		head, j, arg_start = line[:endidx], 0, 0
		while j < len(head):
			if head[j] == '\\':
				j += 2
				continue
			if head[j] == ' ':
				arg_start = j + 1
			j += 1
		unescaped      = shell_unescape(line[arg_start:endidx])
		pattern        = expand(unescaped)
		prefix_escaped = line[arg_start:begidx]
		results = []
		for m in lister(pattern):
			if not m.startswith(pattern):
				continue
			escaped = shell_escape(unescaped + m[len(pattern):])
			if escaped.startswith(prefix_escaped):
				results.append(escaped[len(prefix_escaped):])
		return results

	@staticmethod
	def _local_lister(pattern):
		out = []
		for m in glob(pattern + '*'):
			if os.path.isdir(m):
				m += '/'
			out.append(m)
		return out

##########################################################################################################
