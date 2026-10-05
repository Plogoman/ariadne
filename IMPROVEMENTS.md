# Suggested improvements

## Completed in the current pass

- Added a dependency-free `unittest` suite for MCP input/output bounds, session-name collision handling, and messenger frame parsing.
- Added CI smoke checks for import, compilation, CLI help/version, and unit tests.
- Bounded MCP request bodies and total responses separately, validated `Content-Length`, and made output truncation UTF-8 safe.
- Added optional TLS for MCP with `--mcp-cert` and `--mcp-key`; loopback remains the default.
- Raised the declared minimum Python version to 3.7, matching the minimum supported by the configured setuptools build backend.
- Added uv project configuration and a checked-in lockfile, with documented `uv sync` / `uv run` development commands.

Still recommended: broaden coverage to transfer-path and session lifecycle behavior, pin or checksum downloadable module assets, and split `engine.py` incrementally behind behavior tests.

## Additional module ideas

This list includes the requested additions and follow-on candidates. Operator-side integrations run locally and do not add Ariadne dependencies. Prefer pinned releases and hashes when implementing downloadable modules.

### Previously suggested

- **Seatbelt (Windows host survey):** Added as `seatbelt`, using the existing `ghostpack` upload and downloading a JSON report for the selected check group. [GhostPack/Seatbelt](https://github.com/GhostPack/Seatbelt)
- **Certipy (AD CS assessment):** Added as the operator-side `certipy` module, limited to its `find` enumeration command. Keep Certipy isolated in its own environment because it needs third-party Python packages and currently requires Python 3.12+. [Certipy usage](https://github.com/ly4k/Certipy/wiki/05-%E2%80%90-Usage) · [installation requirements](https://github.com/ly4k/Certipy/wiki/04-%E2%80%90-Installation)
- **enum4linux-ng (SMB enumeration):** Added as the operator-side `enum4linux_ng` module for SMB and domain enumeration, with JSON/YAML export available through its options. It depends on Samba utilities, so Ariadne invokes the local install rather than uploading it to a target. [cddmp/enum4linux-ng](https://github.com/cddmp/enum4linux-ng)

### More Windows and Active Directory candidates

- **Rubeus (Kerberos assessment):** A top priority for an authorized AD workflow. Rubeus is already included in the `ghostpack` upload bundle. Add a dedicated Ariadne command to run a selected operation and capture its output, with ticket-changing or credential-sensitive operations clearly identified and confirmed. [GhostPack/Rubeus](https://github.com/GhostPack/Rubeus)
- **Certify (AD CS assessment from the target):** Certify is already in the `ghostpack` bundle. Add a dedicated target-side AD CS assessment command; keep Certipy as the alternative for operator-side environments. [GhostPack/Certify](https://github.com/GhostPack/Certify)
- **SharpUp (focused Windows privilege checks):** SharpUp is already in the `ghostpack` bundle. Add a small command to run its focused checks and collect the report. It overlaps with WinPEAS, so its value is faster, narrower output rather than another upload source. [GhostPack/SharpUp](https://github.com/GhostPack/SharpUp)

These four binaries are present in the currently configured [GhostPack compiled-binaries archive](https://github.com/r3motecontrol/Ghostpack-CompiledBinaries), which Ariadne already downloads through `upload_ad_scripts ghostpack`. Prioritize command wrappers and output handling over fetching another copy of each binary.

Beyond the four requested changes (module separation, emoji removal, random
session names, uv packaging), here's what would most improve this
codebase going forward, roughly ordered by impact vs. effort.

## Correctness & safety

- **Automated tests.** The initial suite covers MCP request validation and
  output limits. Extend it to cover `Messenger` framing/unframing, random
  session-name collision handling, `Options` validation, and transfer-path
  helpers in `utils.py` (`safe_tar_extractall`, `shell_expand_remote_path`).
- **CI coverage.** CI now runs compile/import/CLI/unit-test smoke checks on
  each push and pull request. Add real session and transfer integration
  coverage when a safe local harness is available.
- **Transport encryption.** Optional TLS is now available for the MCP HTTP
  server. The shell listener transport remains plaintext TCP; TLS support
  there would require a separate compatibility and deployment design.
- **`safe_tar_extractall` and friends deserve a focused security read.**
  Path-traversal handling in file-transfer code is exactly the kind of
  logic that's easy to get subtly wrong and expensive to get wrong in
  practice. Worth a dedicated audit (and tests) independent of this
  refactor.

## Code health

- **Replace `from .compat import *` with explicit imports.** Star imports
  are what caused several of the `NameError`s during this refactor
  (`__program__`, `_REMOTE_PATH_VARIABLE` are excluded from `*` because
  they're underscore-prefixed, and it's easy to miss that a name a module
  uses isn't actually re-exported). Explicit imports would have surfaced
  every one of those at `import` time via a normal `ImportError` instead
  of requiring manual tracing, and make it obvious at a glance what each
  module actually depends on.
- **Reduce the singleton-injection surface.** The `core`/`menu`/`options`/
  `logger`/`input`-as-global pattern was kept to make the module split
  low-risk, but it's still implicit global state assigned post-import in
  `__init__.py`. A longer-term cleanup would thread these through
  explicit constructor arguments or a small context/registry object
  instead of monkey-patching module attributes. Bigger effort, but it
  would make the dependency graph between modules actually visible from
  imports alone.
- **Type hints on the most-touched surfaces.** `Core`, `Session`,
  `Options`, and `Messenger` are the objects everything else depends on;
  typing their public methods (even without a strict mypy gate) would
  make the next refactor safer and give editors real autocomplete.
- **`engine.py` is still 4,100+ lines.** It was kept as one cohesive
  module deliberately (it's genuinely one connected subsystem — `Session`,
  `MainMenu`, `Stream`, the embedded agent code), but `MainMenu`'s `do_*`
  command handlers could reasonably move to their own `commands.py`,
  separate from the `Session`/connection-handling classes.
- **Trim dead/commented-out code.** There are several commented-out
  blocks (alternate-buffer detection in `core.py`'s event loop, old
  `readline.set_auto_history` calls in `cli_input.py`) left from earlier
  iterations. Worth either finishing or deleting.

## Operational

- **Structured logging.** Logs are currently human-formatted strings.
  Adding an optional `--json-logs` or similar would make this usable in
  automated engagement tooling/SIEM pipelines, not just an interactive
  terminal.
- **Config file validation.** `ariadnerc` is loaded via a raw
  `exec(rc.read(), globals())` against the full singleton namespace. This
  is powerful (anything the user can do in the tool, the rc file can do)
  but also means a malformed or malicious rc file can do arbitrary things
  with no feedback beyond a Python traceback. At minimum, wrapping the
  `exec` call to report errors more cleanly would help; a more
  restricted, declarative config format would be a bigger but more robust
  change.
- **Session naming: configurable word lists.** The adjective/noun lists
  are currently fixed in `compat.py`. Letting an engagement supply its own
  word lists (via `ariadnerc` or a CLI flag) would be a small addition
  with real operational value — e.g. codenames matching an engagement's
  cover terminology.

## Packaging

- **Revisit the minimum Python version periodically.** The declared floor is
  now Python 3.7 to match the configured build backend. Raise it when the
  supported runtime matrix or standard-library needs make that worthwhile.
- **Keep `uv.lock` current.** CI and contributor instructions now check that
  project metadata and the committed lockfile stay in sync.
