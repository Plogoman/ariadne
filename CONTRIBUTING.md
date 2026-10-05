# Contributing to Ariadne

Thanks for your interest in contributing! A few principles shape what fits the
project, so reading this first will save us both some back-and-forth.

This is a fork of [Penelope](https://github.com/brightio/penelope) by
brightio; most of what made sense to contribute upstream still applies here,
with one notable exception (the single-script constraint, see below).

## Design principles

- **Standard library only.** Ariadne runs with no installation beyond `uv
  tool install .` (or a plain venv), often somewhere you can't `pip install`
  anything extra. A feature that needs a third-party package is a big ask. If
  you think one is warranted, open an issue to discuss it before writing the
  code.
- **Python 3.7+.** Avoid syntax and stdlib features newer than 3.7. Check before
  relying on something recent.
- **Unix-like handler.** The handler runs on Linux and macOS. It manages shells
  from Windows targets but isn't meant to run on Windows itself.
- **Package, not a single script.** Unlike upstream Penelope, this fork is
  split into a `ariadne/` package (`compat`, `display`, `log`, `cli_input`,
  `options`, `payload_data`, `network`, `messenger`, `core`, `utils`,
  `modules`, `fileserver`, `engine`, `mcp_server`, `__init__`). Put new code in
  the module it logically belongs to rather than piling onto `engine.py`.
  `core`, `menu`, `options`, `logger`, and a few others are late-bound
  singletons injected into each module from `ariadne/__init__.py` (see the
  `# X: injected by ariadne/__init__.py` comments at the top of each file) —
  if you add a new bare-global dependency, add its injection line there too.

## Issues

Please use the templates instead of a blank issue. Bug reports should include
your Ariadne version, install method, the Python version and OS running
Ariadne, the mode in use, the exact command, and expected vs. actual behavior.
Feature requests should describe the problem before the solution. For usage
questions, use Discussions.

**Security vulnerabilities:** do not open a public issue. See
[SECURITY.md](.github/SECURITY.md) for private reporting.

## Pull requests

Use uv for the development environment: `uv sync --locked` followed by
`uv run --locked python -m unittest discover -s tests -v`. If project metadata
or dependencies change, regenerate and commit `uv.lock` with `uv lock`.

1. Keep it focused, one logical change per PR.
2. Match the existing style. Ariadne uses tabs; don't reformat surrounding code.
3. Test it and say how, including the OS, Python version, and mode you tested
   (reverse/bind, Unix/Windows target, raw/PTY). Test on older Python where you
   can.
4. Update the README or help text if usage changes.
5. Fill in the PR template.

Test on two separate machines (one running Ariadne, one acting as the target)
rather than a single local host, since that's how the tool is actually used. At
a minimum, exercise the function you changed; ideally check that the other
functions still work too, so your change doesn't break anything elsewhere.

`ariadne --help` (or `uv run ariadne --help` without installing) is a fast way
to confirm the package still imports and runs on a given Python version.

## License

Ariadne is licensed under GPL-3.0-or-later, the same as upstream Penelope. By
contributing, you agree your contributions are licensed under the same terms.
