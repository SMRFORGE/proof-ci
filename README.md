# proof-ci

An agent-native CI layer: fast, structured, local-first checks for coding agents.

This repository is being built milestone by milestone against the build
specification in [`docs/spec/proof-ci_build_spec.md`](docs/spec/proof-ci_build_spec.md).
The current state is **M1 (package scaffold)**: the package installs, and the
`proof-ci` command reports its version and help. No other commands exist yet.

The full README described in Section 5.18 of the specification is written in
milestone M12.

## Development install

Install into the project's own environment (a virtual environment is recommended):

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"        # Linux / macOS
.venv\Scripts\pip install -e ".[dev]"    # Windows
proof-ci --version
```

## License

MIT. See [`LICENSE`](LICENSE).
