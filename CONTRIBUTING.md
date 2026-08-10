# Contributing to Buoy

Thanks for considering a contribution! Buoy is a small, focused project and welcomes improvements of all sizes — from typo fixes to new plugins.

## Quick Start (Development)

```bash
# Clone
git clone https://github.com/gfargo/buoy.git
cd buoy

# Install in editable mode with dev deps
pip install -e ".[dev]"

# Run in demo mode (no Docker socket needed)
python -m buoy --demo

# Run with hot-reload + debug logging (auto-restarts on Python file changes)
python -m buoy --dev --demo

# Open http://localhost:8090
```

## Project Structure

```
buoy/
├── src/buoy/              # Python backend
│   ├── __main__.py        # CLI entry point
│   ├── server.py          # Starlette app, routes, WebSocket
│   ├── config.py          # YAML config loader + validation
│   ├── auth.py            # Optional auth middleware
│   ├── storage.py         # SQLite ring buffer (24h history)
│   ├── alerts.py          # Threshold detection + notifications
│   ├── services.py        # Docker service discovery
│   ├── demo.py            # Mock data for --demo mode
│   ├── collectors/        # Async data gatherers
│   │   ├── system.py      # CPU, memory, temp, uptime
│   │   ├── docker.py      # Container list, stats, logs
│   │   ├── disk.py        # Mounts, NVMe SMART
│   │   └── network.py     # Fleet peer polling
│   └── plugins/           # Plugin system
│       ├── protocol.py    # Base class + data types
│       ├── loader.py      # Discovery + lifecycle
│       └── builtin/       # Shipped plugins
├── static/                # Frontend (no build step)
│   ├── index.html         # Shell HTML
│   ├── css/               # Styles + themes
│   └── js/                # ES modules
├── plugins/               # User plugin directory
├── tests/                 # pytest test suite
└── docs/                  # Documentation
```

## Running Tests

```bash
# Run all tests
pytest

# With coverage
pytest --cov=buoy --cov-report=term-missing

# Lint
ruff check src/ tests/

# Format check
ruff format --check src/ tests/
```

## Making Changes

### For bug fixes:
1. Create a branch: `git checkout -b fix/description`
2. Write a test that reproduces the bug
3. Fix the bug
4. Verify tests pass: `pytest`
5. Submit a PR

### For new features:
1. Open an issue first to discuss the approach
2. Create a branch: `git checkout -b feat/description`
3. Implement with tests
4. Update docs if applicable
5. Submit a PR

### For new plugins:
1. Create `src/buoy/plugins/builtin/your_plugin.py`
2. Subclass `Plugin` from `buoy.plugins.protocol`
3. Implement `collect()` method
4. Add config schema to `buoy.yaml.example`
5. Implement `render()` to describe your panel using the declarative blocks in
   `buoy.plugins.panel` (`text`, `table`, `keyvalue`, `badges`, `bar`,
   `sparkline`, `list_`) — trusted frontend code (`static/js/panel.js`) turns
   these into HTML and escapes every value for you. `frontend_js()` (raw JS
   executed via `new Function()`) still works but is a deprecated escape
   hatch — it can't be used under a strict CSP and every value must be
   escaped by hand.

See [docs/plugins.md](docs/plugins.md) for the full guide.

## Code Style

- Python: formatted with `ruff` (line length 100)
- Frontend: vanilla JS (ES modules), no build step, no framework
- Keep it simple — stdlib where possible, external deps only when justified
- Type hints on all public functions

## Architecture Principles

- **Single container** — no external DB, no build step for users
- **Config-driven** — behavior changes via `buoy.yaml`, not code changes
- **Graceful degradation** — missing features return zeros, not errors
- **Plugin-first** — integrations belong in plugins, not core
- **No magic** — explicit > implicit, clear error messages

## Pull Request Guidelines

- Keep PRs focused (one concern per PR)
- Include tests for new functionality
- Use [Conventional Commits](https://www.conventionalcommits.org/) for your commit messages / PR title
  (`feat:`, `fix:`, `chore:`, etc.) — [release-please](https://github.com/googleapis/release-please)
  uses these to generate `CHANGELOG.md` and pick the next version automatically. `feat:` → minor bump,
  `fix:` → patch bump, `feat!:`/`BREAKING CHANGE:` → major bump.
- Don't hand-edit `CHANGELOG.md` or bump the version yourself — release-please's release PR does both
- CI must pass (tests + lint + format + Docker build)

## Release Process

Releases are automated via [release-please](https://github.com/googleapis/release-please):

1. Every merge to `main` updates (or opens) a standing "chore(main): release X.Y.Z" PR with the
   version bump (`pyproject.toml` + `src/buoy/__init__.py`) and a generated `CHANGELOG.md` entry.
2. Merging that PR tags the release and publishes the GitHub Release.
3. The tag push triggers `.github/workflows/release.yml`, which builds and pushes the multi-arch
   image to `ghcr.io/gfargo/buoy` (`latest`, `X.Y.Z`, `X.Y`, and short-SHA tags).

No manual version bumps or tags — just merge PRs with conventional commit messages.

## Reporting Issues

- Use the GitHub issue templates
- Include: buoy version, config (redacted), what you expected, what happened
- For crashes: include the full traceback

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
