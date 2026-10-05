#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *
from .compat import __program__
from .messenger import Messenger
from .display import paint

# logger, cmdlogger: injected by ariadne/__init__.py after they exist.

# Setup for ephemeral mode
_ephemeral_root = None
_ram = None


def _cleanup_ephemeral():
	if _ephemeral_root is not None:
		shutil.rmtree(_ephemeral_root, ignore_errors=True)


if '--no-disk' in sys.argv:
	_ram = Path("/dev/shm") if Path("/dev/shm").is_dir() and os.access("/dev/shm", os.W_OK) else None
	_ephemeral_root = Path(tempfile.mkdtemp(prefix="ariadne-", dir=str(_ram) if _ram else None))
	tempfile.tempdir = str(_ephemeral_root)
	atexit.register(_cleanup_ephemeral)


class Options:
	log_levels = {"silent":'WARNING', "debug":'DEBUG'}

	def __init__(self):
		real_home = Path.home()
		sudo_user = os.environ.get("SUDO_USER")
		if sudo_user:
			real_home = Path(pwd.getpwnam(sudo_user).pw_dir)

		self.basedir = globals().get('_ephemeral_root') or (real_home / f'.{__program__}')
		self.default_listener_port = 4444
		self.default_bindshell_port = 5555
		self.default_fileserver_port = 8000
		self.default_interface = "0.0.0.0"
		self.payloads = False
		self.no_log = False
		self.no_disk = False
		self.no_timestamps = False
		self.no_colored_timestamps = False
		self.max_maintain = 5
		self.maintain = 1
		self.max_sessions = 5
		self.single_session = False
		self.no_attach = False
		self.no_upgrade = False
		self.keep_history = False
		self.keep_bracketed_paste = False
		self.debug = False
		self.dev_mode = False
		self.latency = .01
		self.histlength = 2000
		self.timeout_short = 25
		self.timeout_long = 60
		self.max_open_files = 5
		self.verify_ssl_cert = True
		self.proxy = ''
		self.upload_chunk_size = 1048576
		self.download_chunk_size = 1048576
		self.network_buffer_size = 32768
		self.compression_level = 1
		self.download_folder = ''
		self.escape = {'sequence':b'\x1b[24~', 'key':'F12'}
		self.logfile = f"{__program__}.log"
		self.debug_logfile = "debug.log"
		self.cmd_histfile = 'cmd_history'
		self.debug_histfile = 'cmd_debug_history'
		self.useragent = "Wget/1.21.2"
		self.upload_random_suffix = False
		self.attach_lines = 20
		self.link_dereference = True
		self.mcp = False
		self.mcp_host = ''
		self.mcp_port = 0
		self.mcp_token = ''
		self.mcp_cert = ''
		self.mcp_key = ''
		self.no_bins = ''

	def __getattribute__(self, option):
		if option in ("logfile", "debug_logfile", "cmd_histfile", "debug_histfile"):
			return self.basedir / super().__getattribute__(option)
#		if option == "basedir":
#			return Path(super().__getattribute__(option))
		return super().__getattribute__(option)

	def __setattr__(self, option, value):
		show = logger.error if 'logger' in globals() else lambda x: print(paint(x).red)
		level = __class__.log_levels.get(option)

		if level:
			level = level if value else 'INFO'
			logging.getLogger(__program__).setLevel(getattr(logging, level))

		elif option == 'maintain':
			if value > self.max_maintain:
				show(f"Maintain value decreased to the max ({self.max_maintain})")
				value = self.max_maintain
			if value < 1:
				value = 1
			#if value == 1: show("Maintain value should be 2 or above")
			if value > 1 and self.single_session:
				show("Single Session mode disabled because Maintain is enabled")
				self.single_session = False
			if getattr(self, 'max_sessions', 0) and self.max_sessions < value:
				show(f"Max sessions per host increased to {value} to satisfy Maintain")
				self.max_sessions = value

		elif option == 'single_session':
			if self.maintain > 1 and value:
				show("Single Session mode disabled because Maintain is enabled")
				value = False

		elif option == 'max_sessions':
			if value < 0:
				value = 0
			if value and value < self.maintain:
				show(f"Max sessions per host increased to {self.maintain} to satisfy Maintain")
				value = self.maintain

		elif option == 'network_buffer_size':
			_max = (1 << (8 * Messenger.LEN_BYTES)) - 1 - Messenger.TYPE_BYTES - Messenger.STREAM_BYTES
			if isinstance(value, int) and value > _max:
				show(f"network_buffer_size capped to {_max} (TLV frame limit)")
				value = _max

		elif option == 'compression_level':
			if isinstance(value, int) and not 0 <= value <= 9:
				value = min(max(value, 0), 9)
				show(f"compression_level clamped to {value} (valid range: 0-9)")

		elif option == 'no_bins':
			if value is None:
				value = []
			elif type(value) is str:
				value = re.split('[^a-zA-Z0-9]+', value)

		elif option == 'ports':
			if value is None:
				value = [None]
			elif type(value) is str:
				value = re.split('[^a-zA-Z0-9]+', value)

		elif option == 'proxy':
			if not value:
				os.environ.pop('http_proxy', '')
				os.environ.pop('https_proxy', '')
			else:
				os.environ['http_proxy'] = value
				os.environ['https_proxy'] = value

		elif option == 'basedir':
			value.mkdir(parents=True, exist_ok=True)

		stored_value = self.__dict__.get(option)
		if option in self.__dict__ and stored_value is not None:
			new_value_type = type(value).__name__
			orig_value_type = type(stored_value).__name__
			if new_value_type == orig_value_type:
				self.__dict__[option] = value
			else:
				show(f"Wrong value type for '{option}': Expect <{orig_value_type}>, not <{new_value_type}>")
		else:
			self.__dict__[option] = value



# Apply default options
options = Options()

if _ephemeral_root is not None:
	options.no_disk = True
	if _ram is None:
		print(paint(f"[!] --no-disk: no tmpfs (/dev/shm) on this platform (e.g. macOS); "
			f"state lives in a temp dir on DISK ({_ephemeral_root}), wiped on exit, but NOT true RAM.").yellow)
