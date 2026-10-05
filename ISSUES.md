# ISSUES.md — state of play (Oct 5, evening)

## FIXED — `run meterpreter` stuck ("handler comes up, no meterpreter session")

The headline bug of the Oct 5 session. Two root causes, both in `ariadne/engine.py`,
both verified by live end-to-end repro on deb13 (agent session, real handler,
`Meterpreter session 1 opened` + `meterpreter >` at 05:56:20).

**Cause 1 — exec_tmp probe self-sabotage (bash history expansion).**
The probe wrote its exec-test script via `echo "#!/bin/sh"`. Ariadne types exec
commands into an *interactive* bash PTY, where history expansion mangles `!`
inside double quotes → `bash: !/bin/sh: event not found` → the probe's test
script never runs → "No writable+executable directory found (noexec?)" on
EVERY interactive session. Consequences:
- standalone-python deploy refused (`need_binary` → `exec_tmp`) → degraded
  raw-shell sessions, agent mode unreachable;
- meterpreter ELF uploaded to `session.tmp` fallback = `/dev/shm`, which is
  **noexec** on deb13 → even a launched payload dies with Permission denied
  (hidden by `>/dev/null 2>&1`).
Fix: single-quote the shebang in the probe (engine.py, exec_tmp property).
Reference: GNU Bash manual §9.3 — history expansion is interactive-only, which
is why manual `docker exec` probes always passed while PTY-typed probes failed.

**Cause 2 — exec marker append breaks background launches (`&;`).**
Non-raw Unix exec appends the completion marker as `{cmd};printf $C$A`. For
`nohup <elf> >/dev/null 2>&1 &` this produces `&;printf` → bash syntax error →
the WHOLE line is rejected → background job never starts, marker never prints,
exec times out. The meterpreter launch (and any `exec('... &')` on the
shell/typing path) silently did nothing.
Fix: commands ending in `&`, `|`, `;` get the marker after a plain space
(`... & printf $C$A`), others keep `;` (engine.py, exec marker build).

**Retired red herrings (from the morning session):**
- "Session died / We lost deb13" — never reproduced today across 4 module runs
  (raw + agent). The death-tracking instrumentation (`/tmp/agent_exit.txt`,
  `/tmp/agent_crash.txt`, dev mode) stays armed but the stuck-handler symptom
  was fully explained by the two causes above. Leftover stuck msfconsole
  windows from earlier attempts (including one misconfigured `aarch64` handler
  from a garbled-arch run) raced on port 5555 and confused diagnosis.
- Upload truncation — 250 B is the CORRECT msfvenom stager size for
  `linux/x64/meterpreter/reverse_tcp` (staged payload; the ~3 MB stage is sent
  by the handler after connect).

**Remaining arch risk (not today's fix):** the meterpreter module re-probes
`uname -m` fresh (works — x86_64 today), but other modules (traitor, ligolo,
chisel, pspy) still trust the setup-time `session.arch`, which can be garbled
by PTY echo interleaving on agent sessions. If a module picks a wrong-arch
binary, suspect that.

## FIXED 2 — integration test flaky: `test_exit_sequence_terminates_script_exec_thread`

Bit the first public CI runs: on a loaded runner the 60 s deploy / 10 s
teardown budgets were too tight ("agent not deployed", "Thread-2 (exec)
survived the exit sequence"). Mitigated in beb0e12 by raising the budgets
(120 s deploy, 30 s teardown) — no code regression was involved; the test
prints a full stack dump if a thread genuinely survives, so real bugs still
fail loudly. If it flakes again despite the budgets, root-cause it.

## ENVIRONMENT QUIRKS (not ariadne bugs)

- deb13: `/dev/shm` is **noexec**; `/tmp` is exec-ok. ariadne's exec_tmp probe
  now handles this correctly (tests executability, skips /dev/shm).
- deb13's system python lacks stdlib pieces → standalone python needed. The
  download/upload re-runs per session: ariadne's `_cleanup_ephemeral` wipes
  target-side caches like `/tmp/vGRX3HzgAr` on clean exit (the old "do NOT rm"
  warning is obsolete — ariadne cleans it itself). Re-deploy costs ~2 min
  (33 MB download + upload at docker-bridge speed).
- Root on deb13 cannot overwrite files owned by foreign uids (zen-kernel
  hardening) — worked around by the tar uid/gid-0 fix.
- linpeas/lse leave hung `dd` processes (DNS check) — kill manually.
- The container dies with SIGHUP when its main bash's terminal goes away —
  `docker start -ai deb13` brings it back.
- Handler windows: `Open(terminal=True)` spawns one Ghostty window per
  `run meterpreter` when the handler port is free. Closing stuck windows by
  hand is safe; with the launch fixed they shouldn't stick anymore.

## COMMANDS

```bash
cd /home/legion/Tools/ariadne && uv tool install --reinstall .
# verify installed == source:
diff -rq --exclude=__pycache__ \
  ~/.local/share/uv/tools/ariadne-shell-handler/lib/python3.14/site-packages/ariadne \
  ~/Tools/ariadne/ariadne && echo OK
# tests:
python3 -m unittest discover -s tests
```

Test count: 62, all green. `tests/test_agent_deploy.py` keeps the deploy
pipeline honest (agent source format, payload compile, instrumentation
markers). If you touch the agent source, `exec()` marker build, or
`upgrade()`, run it.
