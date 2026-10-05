#!/usr/bin/env python3

# Part of Ariadne Shell Handler (a fork of Penelope, GPL-3.0-or-later). See LICENSE.

from .compat import *
from .compat import _REMOTE_PATH_VARIABLE
from .options import options
from .log import logger, cmdlogger
from .display import paint, PBar, Size
from .cli_input import ask
from .payload_data import URLS

def get_glob_size(_glob, block_size, dereference=False):
	_stat = os.stat if dereference else os.lstat
	from glob import glob
	from math import ceil
	def size_on_disk(filepath):
		try:
			return ceil(float(_stat(filepath).st_size) / block_size) * block_size
		except Exception:
			return 0
	total_size = 0
	for part in shlex.split(_glob):
		p = normalize_path(part)
		for item in (glob(p) or ([p] if os.path.lexists(p) else [])):
			if os.path.isfile(item):
				total_size += size_on_disk(item)
			elif os.path.isdir(item):
				for root, dirs, files in os.walk(item):
					for file in files:
						filepath = os.path.join(root, file)
						total_size += size_on_disk(filepath)
	return total_size



def _is_within_directory(directory, target):
	target = os.path.realpath(target)
	try:
		return os.path.commonpath([directory]) == os.path.commonpath([directory, target])
	except ValueError:
		return False



def shell_expand_remote_path(path):
	word, cursor = [], 0
	tilde = re.match(r'~[A-Za-z0-9._-]*(?=/|$)', path)
	if tilde:
		word.append('"${HOME?}"' if tilde.group() == '~' else tilde.group())
		cursor = tilde.end()

	for variable in _REMOTE_PATH_VARIABLE.finditer(path, cursor):
		if variable.start() > cursor:
			word.append(shlex.quote(path[cursor:variable.start()]))
		name = variable.group(1) or variable.group(2)
		word.append(f'"${{{name}?}}"')
		cursor = variable.end()

	if cursor < len(path):
		word.append(shlex.quote(path[cursor:]))
	return ''.join(word) or "''"



def _transfer_token_span(arguments, cursor, windows=False, single_quotes=True):
	while cursor < len(arguments) and arguments[cursor].isspace():
		cursor += 1
	if cursor == len(arguments):
		return None

	start = cursor
	quote = None
	escaped = False
	quote_chars = "'\"" if single_quotes else '"'
	while cursor < len(arguments):
		char = arguments[cursor]
		if escaped:
			escaped = False
		elif char == '\\' and not windows and quote != "'":
			escaped = True
		elif quote:
			if char == quote:
				quote = None
		elif char in quote_chars:
			quote = char
		elif char.isspace():
			break
		cursor += 1
	return start, cursor, quote is None



def completing_transfer_output(line, cursor, source_windows=False, destination_windows=False):
	prefix = (line or '')[:cursor]
	position = 0
	while True:
		span = _transfer_token_span(prefix, position, source_windows,
			single_quotes=not source_windows)
		if span is None:
			return False
		begin, end, _ = span
		token = prefix[begin:end]
		position = end
		if token == '--':
			return False
		if token not in ('-o', '--output'):
			continue

		value = _transfer_token_span(prefix, position, destination_windows)
		if value is None:
			return True
		_, value_end, closed = value
		if not closed or value_end == len(prefix):
			return True
		return False



def parse_transfer_output(arguments, source_windows=False, destination_windows=False):
	arguments = arguments or ''
	position = 0
	while True:
		span = _transfer_token_span(arguments, position, source_windows,
			single_quotes=not source_windows)
		if span is None:
			break
		begin, end, _ = span
		token = arguments[begin:end]
		position = end
		if token == '--':
			return (arguments[:begin] + arguments[end:]).strip(), None
		if token not in ('-o', '--output'):
			continue

		folder_span = _transfer_token_span(arguments, position, destination_windows)
		if folder_span is None:
			break
		folder_begin, folder_end, closed = folder_span
		if not closed:
			break
		folder = arguments[folder_begin:folder_end]
		if destination_windows:
			if len(folder) >= 2 and folder[0] == folder[-1] and folder[0] in "'\"":
				folder = folder[1:-1]
			parts = [folder] if folder else None
		else:
			try:
				parts = shlex.split(folder, posix=True)
			except ValueError:
				parts = None
		if parts and len(parts) == 1:
			return (arguments[:begin] + arguments[folder_end:]).strip(), parts[0]
		break

	return arguments.strip(), None



def safe_tar_extractall(tar, dest, streaming=False, strip_prefixes=None):
	dest_real = os.path.realpath(dest)
	orig_extract_member = tar._extract_member
	extracted = []

	def guarded(tarinfo, targetpath, *args, **kwargs):
		if strip_prefixes:
			name = tarinfo.name.lstrip("/")
			for pref in strip_prefixes:
				if pref and (name == pref or name.startswith(pref + "/")):
					name = name[len(pref):].lstrip("/")
					break
			if not name:
				return
			tarinfo.name = name
			targetpath = os.path.join(dest_real, name)

		if not _is_within_directory(dest_real, targetpath):
			logger.error(str(paint("<LOCAL>").yellow) + " " +
				str(paint(f"Refusing unsafe path in archive: {tarinfo.name}").red))
			return

		if not (tarinfo.isreg() or tarinfo.isdir() or tarinfo.issym() or tarinfo.islnk()):
			logger.error(str(paint("<LOCAL>").yellow) + " " +
				str(paint(f"Refusing special file in archive: {tarinfo.name}").red))
			return

		if tarinfo.issym() or tarinfo.islnk():
			base = os.path.dirname(os.path.realpath(targetpath)) if tarinfo.issym() else dest_real
			link_path = os.path.join(base, tarinfo.linkname)
			if not _is_within_directory(dest_real, link_path):
				logger.error(str(paint("<LOCAL>").yellow) + " " +
					str(paint(f"Refusing unsafe link in archive: {tarinfo.name} -> {tarinfo.linkname}").red))
				return
			if streaming:
				try:
					os.makedirs(os.path.dirname(targetpath), exist_ok=True)
					if os.path.lexists(targetpath):
						os.remove(targetpath)
					if tarinfo.issym():
						os.symlink(tarinfo.linkname, targetpath)
					else:
						os.link(link_path, targetpath)
					extracted.append(targetpath)
				except OSError as e:
					logger.error(str(paint("<LOCAL>").yellow) + " " +
						str(paint(f"Skipping link {tarinfo.name}: {e}").red))
				return

		elif os.path.islink(targetpath):
			os.remove(targetpath)

		tarinfo.mode &= ~0o6000
		tarinfo.mode |= 0o200
		orig_extract_member(tarinfo, targetpath, *args, **kwargs)
		extracted.append(targetpath)

	tar._extract_member = guarded
	try:
		if hasattr(tarfile, 'data_filter'):
			tar.extractall(dest, filter='fully_trusted')
		else:
			tar.extractall(dest)
	finally:
		try:
			del tar._extract_member
		except AttributeError:
			tar._extract_member = orig_extract_member
	return extracted



def windows_zip_script(remote_items, archive_path):
	payload = base64.b64encode(json.dumps({
		'archive': archive_path,
		'paths': remote_items,
	}).encode()).decode()
	return dedent(rf'''
	$ErrorActionPreference = 'Stop'
	Add-Type -AssemblyName System.IO.Compression
	Add-Type -AssemblyName System.IO.Compression.FileSystem
	$payload = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}')))
	$archivePath = [string]$payload.archive
	$sourcePaths = @($payload.paths | ForEach-Object {{ [string]$_ }})
	$archive = $null
	$roots = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)

	function Add-ZipFile([string]$path, [string]$entryName) {{
		$entryName = $entryName.Replace('\', '/')
		[IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
			$script:archive, $path, $entryName, [IO.Compression.CompressionLevel]::Optimal
		) | Out-Null
	}}

	function Add-ZipDirectory([string]$path, [string]$entryRoot) {{
		$entryRoot = $entryRoot.Replace('\', '/').TrimEnd('/')
		$script:archive.CreateEntry($entryRoot + '/') | Out-Null
		foreach ($child in Get-ChildItem -LiteralPath $path -Force -ErrorAction Stop) {{
			$childEntry = $entryRoot + '/' + $child.Name
			if ($child.PSIsContainer) {{
				if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {{
					$script:archive.CreateEntry($childEntry.Replace('\', '/').TrimEnd('/') + '/') | Out-Null
				}} else {{
					Add-ZipDirectory $child.FullName $childEntry
				}}
			}} else {{
				Add-ZipFile $child.FullName $childEntry
			}}
		}}
	}}

	function Resolve-RequestedPath([string]$requestedPath) {{
		if ($requestedPath.IndexOfAny([char[]]'*?') -ge 0) {{
			$pattern = [Management.Automation.WildcardPattern]::Escape($requestedPath)
			$pattern = $pattern.Replace('`*', '*').Replace('`?', '?')
			$items = @(Get-Item -Path $pattern -Force -ErrorAction Stop)
			if ($items.Count -eq 0) {{ throw "Path did not match any items: $requestedPath" }}
			return $items
		}}
		return @(Get-Item -LiteralPath $requestedPath -Force -ErrorAction Stop)
	}}

	try {{
		$archive = [IO.Compression.ZipFile]::Open($archivePath, [IO.Compression.ZipArchiveMode]::Create)
		foreach ($requestedPath in $sourcePaths) {{
			foreach ($item in @(Resolve-RequestedPath $requestedPath)) {{
				$rootName = $item.Name
				if ([string]::IsNullOrEmpty($rootName)) {{ $rootName = $item.PSDrive.Name }}
				if (-not $roots.Add($rootName)) {{
					throw "Multiple requested items have the same archive root name: $rootName"
				}}
				if ($item.PSIsContainer) {{
					Add-ZipDirectory $item.FullName $rootName
				}} else {{
					Add-ZipFile $item.FullName $rootName
				}}
			}}
		}}
	}} catch {{
		Write-Error $_
		exit 1
	}} finally {{
		if ($null -ne $archive) {{ $archive.Dispose() }}
	}}

	[Convert]::ToBase64String([IO.File]::ReadAllBytes($archivePath))
	Remove-Item -LiteralPath $archivePath -Force
	''').strip() + '\n'



def url_to_bytes(URL):

	# URLs with special treatment
	URL = re.sub(
		r"https://www.exploit-db.com/exploits/",
		"https://www.exploit-db.com/download/",
		URL
	)

	req = Request(URL, headers={'User-Agent': options.useragent})

	logger.trace(paint(f"Downloading URL: {URL}").cyan)
	ctx = ssl.create_default_context() if options.verify_ssl_cert else ssl._create_unverified_context()

	while True:
		try:
			response = urlopen(req, context=ctx, timeout=options.timeout_short)
			break
		except (HTTPError, TimeoutError) as e:
			logger.error(e)
		except URLError as e:
			logger.error(e.reason)
			if (hasattr(ssl, 'SSLCertVerificationError') and type(e.reason) == ssl.SSLCertVerificationError) or\
				(isinstance(e.reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(e)):
				answer = ask("Cannot verify SSL Certificate. Download anyway? (y/N): ")
				if answer.lower() == 'y': # Trust the cert
					ctx = ssl._create_unverified_context()
					continue
			else:
				answer = ask("Connection error. Try again? (Y/n): ")
				if answer.lower() == 'n':
					pass
				else:
					continue
		return None, None

	filename = response.headers.get_filename()
	if filename:
		filename = filename.strip('"')
	else:
		url_path = urlsplit(response.geturl()).path
		path_parts = [part for part in url_path.split('/') if part]
		filename = unquote(path_parts[-1]) if path_parts else ''

	filename = os.path.basename((filename or '').replace('\\', '/'))
	if filename in ('', '.', '..'):
		filename = f"download_{int(time.time())}"

	size_header = response.headers.get('Content-Length')
	try:
		size = int(size_header) if size_header is not None else None
		if size is not None and size < 0:
			raise ValueError
	except (TypeError, ValueError):
		logger.warning(f"Invalid Content-Length: {size_header!r}")
		size = None

	data = bytearray()
	pbar = None
	if size is not None and size > 0:
		pbar = PBar(size, caption=" ", barlen=30, metric=Size, reverse=True)

	while True:
		try:
			chunk = response.read(options.network_buffer_size)
			if not chunk:
				break
			data.extend(chunk)
			if pbar is not None:
				pbar.update(len(chunk))
		except Exception as e:
			if pbar is not None:
				pbar.terminate()
			logger.error(e)
			return None, None

	if pbar is not None:
		pbar.terminate()

	return filename, data



def check_urls():
	from concurrent.futures import ThreadPoolExecutor, as_completed
	threads = 10
	global URLS
	urls = URLS.values()
	space_num = len(max(urls, key=len))
	all_ok = True

	def _probe(url):
		req = Request(url, method="HEAD", headers={'User-Agent': options.useragent})
		try:
			with urlopen(req, timeout=5) as response:
				return url, response.getcode(), None
		except HTTPError as e:
			return url, e.code, None
		except Exception as e:
			return url, None, e

	with ThreadPoolExecutor(threads) as ex:
		futures = {ex.submit(_probe, url): url for url in urls}
		for fut in as_completed(futures):
			url, status_code, err = fut.result()
			if err is not None:
				status_code = err
				all_ok = False
			elif status_code >= 400:
				all_ok = False
			if __name__ == '__main__':
				color = 'RED' if isinstance(status_code, int) and status_code >= 400 or err else 'GREEN'
				print(f"{paint(url).cyan}{paint('.').DIM * (space_num - len(url))} => {getattr(paint(status_code), color)}")
	return all_ok
