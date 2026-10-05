#!/usr/bin/env python3

# Copyright (c) 2021 - 2026 brightio <brightiocode@gmail.com>
# Ariadne Shell Handler -- a fork of Penelope (https://github.com/brightio/penelope)
# by brightio, GPL-3.0-or-later. See LICENSE.
# This module is one piece of a split of the original single-file
# penelope.py into packages, done for separation of concerns.

__program__ = "ariadne"
__version__ = "0.21.13"

import os
import io
import re
import sys
import pwd
import tty
import ssl
import time
import gzip
import json
import zlib
import shlex
import queue
import codecs
import struct
import shutil
import atexit
import socket
import signal
import base64
import secrets
import termios
import tarfile
import logging
import zipfile
import inspect
import tempfile
import platform
import itertools
import traceback
import threading
import subprocess
import socketserver

from math import ceil
from glob import glob
from json import dumps
from code import interact
from errno import EADDRINUSE, EADDRNOTAVAIL
from select import select
from pathlib import Path, PureWindowsPath
from argparse import ArgumentParser, RawTextHelpFormatter
from datetime import datetime
from textwrap import indent, dedent
from binascii import Error as binascii_error
from functools import wraps
from contextlib import ExitStack
from collections import deque, defaultdict
from urllib.parse import unquote, quote, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

################################## PYTHON MISSING BATTERIES ####################################
from string import ascii_letters
from random import choice, randint
rand = lambda _len: ''.join(choice(ascii_letters) for i in range(_len))

# Word lists used to generate human-readable, random session names (e.g. "silent-falcon")
# instead of sequential numeric IDs. Kept short and dependency-free on purpose.
SESSION_NAME_ADJECTIVES = (
	'silent', 'crimson', 'shadow', 'phantom', 'rogue', 'covert', 'feral', 'grim',
	'swift', 'lucid', 'obscure', 'rabid', 'sly', 'vicious', 'wry', 'bleak',
	'cunning', 'rusty', 'gilded', 'hollow', 'acrid', 'brazen', 'dire', 'fierce',
	'jagged', 'murky', 'nimble', 'quiet', 'sinister', 'venomous',
)
SESSION_NAME_NOUNS = (
	'falcon', 'viper', 'jackal', 'raven', 'cobra', 'ghost', 'wolf', 'hornet',
	'mantis', 'badger', 'lynx', 'kestrel', 'adder', 'panther', 'scorpion', 'owl',
	'hawk', 'serpent', 'wraith', 'specter', 'jaguar', 'puma', 'orca', 'shrike',
	'vulture', 'weasel', 'stoat', 'marten', 'pike', 'mamba',
)
caller = lambda: inspect.stack()[2].function
#bdebug = lambda file, data: open("/tmp/" + file, "a").write(repr(data) + "\n")
chunks = lambda string, length: (string[0 + i:length + i] for i in range(0, len(string), length))
pathlink = lambda path: f'\x1b]8;;file://{quote(str(path.parents[0]))}\x07{sanitize_meta(str(path.parents[0]))}{os.path.sep}\x1b]8;;\x07\x1b]8;;file://{quote(str(path))}\x07{sanitize_meta(path.name)}\x1b]8;;\x07'
normalize_path = lambda path: os.path.normpath(os.path.expandvars(os.path.expanduser(path)))
shell_unescape = lambda s: re.sub(r'\\(.)', r'\1', s)
shell_escape   = lambda s: ''.join((chr(92) + c if c in (' ' + chr(39) + chr(34) + chr(92) + ';&(|<>=:') else c) for c in s)
shell_escape_glob = lambda s: re.sub(r'[^\w@%+=:,./~*?\[\]-]', lambda m: '\\' + m.group(0), s)
visible_len   = lambda s: len(re.sub(r'\x1b\[[0-9;]*m|[\x01\x02]', '', str(s)))
sanitize_meta = lambda s: ''.join(c for c in s if c.isprintable()) if isinstance(s, str) else s
HTTP_CONTROL_CHAR_TABLE = {char: r'\x{:02x}'.format(char) for char in list(range(32)) + list(range(127, 160))}
HTTP_CONTROL_CHAR_TABLE[ord('\\')] = r'\\'

_REMOTE_PATH_VARIABLE = re.compile(
	r'\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})'
)



# Environment / terminal constants (pure, side-effect-free; safe to
# compute eagerly at import time instead of at CLI-startup as the
# original single-file version did).
myOS = platform.system()
DISPLAY = 'DISPLAY' in os.environ
TERMINALS = [
	'gnome-terminal', 'mate-terminal', 'qterminal', 'terminator', 'alacritty', 'kitty', 'tilix',
	'konsole', 'xfce4-terminal', 'lxterminal', 'urxvt', 'st', 'xterm', 'eterm', 'x-terminal-emulator'
]
MAX_CMD_PROMPT_LEN = 335
LOG_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S: "
LINUX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin"

def terminal_emulator():
	candidates = []
	if os.environ.get('TERMINAL'):
		candidates.append(os.environ['TERMINAL'])
	candidates += ['x-terminal-emulator', 'xdg-terminal-exec', *TERMINALS]
	return next((term for term in candidates if shutil.which(term)), None)
