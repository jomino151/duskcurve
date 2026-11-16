# Contributing

## Setup

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install -e '.[dev]'
```

The venv needs `--system-site-packages` because PyGObject comes from the system `python3-gi` package.

To run your checkout as the installed daemon, use `./install.sh --dev`.

## Checks

Run these before opening a pull request:

```bash
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
```

GTK widgets and the shell extension have no automated tests; describe how you checked them by hand.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/) prefixes: `feat`, `fix`, `perf`, `refactor`, `test`, `docs`, `style`, `chore`. Add a scope where it helps, e.g. `fix(daemon): ...`.

Update `CHANGELOG.md` under `Unreleased` for user-facing changes.
