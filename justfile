# Default recipe - show help
default:
    @just --list --unsorted

# ─── Setup ────────────────────────────────────────────────────────────

# Install all dependencies
[group: 'setup']
install:
    uv sync

# Install full local dev toolchain: uv deps + commitlint (bun) + git hooks
[group: 'setup']
install-dev: install
    bun install
    just install-lefthook

# Install the `doxa` command onto PATH, isolated from this checkout.
#
# Deliberately NOT `cp doxa /usr/local/bin/`. Since ./doxa became a shim over
# `uv run doxa`, copying it outside the checkout produces a command that
# re-executes itself: uv resolves `doxa` from PATH, finds the copy, and runs it
# again. `uv tool install` installs the package's own console script into its
# own environment, so the installed command has no dependency on this directory.
[group: 'setup']
install-bin:
    uv tool install --force .
    @echo "Installed. Ensure uv's tool bin directory is on PATH: uv tool update-shell"

# Check environment dependencies
[group: 'setup']
check:
    uv run ruff check src/doxa_research/ --fix
    uv run ty check src/doxa_research/

# Clean build artifacts
[group: 'setup']
clean:
    rm -rf build/ dist/ *.egg-info
    rm -rf .pytest_cache/ .ruff_cache/
    rm -rf htmlcov/ .coverage
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    find . -type f -name ".DS_Store" -delete 2>/dev/null || true

# ─── Quality ──────────────────────────────────────────────────────────

# Run all checks (format, lint, typecheck, security, test)
[group: 'quality']
all: format lint typecheck security test

# Format src/doxa_research/ package
[group: 'quality']
format:
    uv run ruff format src/doxa_research/

# Lint src/doxa_research/ package
[group: 'quality']
lint:
    uv run ruff check src/doxa_research/ --fix

# Type check src/doxa_research/ package
[group: 'quality']
typecheck:
    uv run ty check src/doxa_research/

# Run bandit security linter
[group: 'quality']
security:
    uvx bandit -r src/ -ll

# Auto-fix and format src/doxa_research/
[group: 'quality']
fix:
    uv run ruff check --fix src/doxa_research/
    uv run ruff format src/doxa_research/

# Format test suite
[group: 'quality']
test-format:
    uv tool run ruff format doxa_test

# Lint test suite
[group: 'quality']
test-lint:
    uv tool run ruff check doxa_test

# Type check test suite
[group: 'quality']
test-typecheck:
    uv tool run ty check doxa_test

# Auto-fix and format test suite
[group: 'quality']
test-fix:
    uv tool run ruff check --fix doxa_test
    uv tool run ruff format doxa_test

# Format both package and test suite
[group: 'quality']
format-all: format test-format

# Lint both package and test suite
[group: 'quality']
lint-all: lint test-lint

# Run all checks on entire codebase
[group: 'quality']
check-all: lint typecheck test-lint test-typecheck

# Auto-fix and format entire codebase
[group: 'quality']
fix-all: fix test-fix

# ─── Testing ──────────────────────────────────────────────────────────

# Run full test suite (pytest in parallel + doxa_test integration suite)
[group: 'testing']
test:
    uv run pytest tests/ -n auto -v
    ./doxa_test -r

# Run pytest suite serially (for debugging xdist flakiness)
[group: 'testing']
test-serial:
    uv run pytest tests/ -v

# Run extended (real-API) contract tests. Gated by `pytest -m extended`;
# requires provider API keys for live provider coverage. P18 Phase I — runs
# nightly via .github/workflows/extended.yml.
[group: 'testing']
test-extended:
    uv run pytest -m "extended and not extended_slow" -v

# Run OpenAI extended tests only.
[group: 'testing']
test-extended-openai:
    uv run pytest -m "extended and provider_openai and not extended_slow" -v

# Run Perplexity extended tests only.
[group: 'testing']
test-extended-perplexity:
    uv run pytest -m "extended and provider_perplexity and not extended_slow" -v

# Run Gemini extended tests only.
[group: 'testing']
test-extended-gemini:
    uv run pytest -m "extended and provider_gemini and not extended_slow" -v

# Run opt-in slow real-API lifecycle tests.
[group: 'testing']
test-extended-slow:
    DOXA_EXTENDED_SLOW=1 uv run pytest -m extended_slow -v

# Run live-API workflow regression tests. Gated by `pytest -m live_api`;
# requires provider API keys for live provider coverage. P20 — runs weekly via
# .github/workflows/live-api.yml (Sat 7pm PDT).
[group: 'testing']
test-live-api:
    uv run pytest -m "live_api and not extended_slow" -v

# Run OpenAI live-API workflow tests only.
[group: 'testing']
test-live-api-openai:
    uv run pytest -m "live_api and provider_openai and not extended_slow" -v

# Run Perplexity live-API workflow tests only.
[group: 'testing']
test-live-api-perplexity:
    uv run pytest -m "live_api and provider_perplexity and not extended_slow" -v

# Run Gemini live-API workflow tests only.
[group: 'testing']
test-live-api-gemini:
    uv run pytest -m "live_api and provider_gemini and not extended_slow" -v

# Run tests skipping interactive mode (fast, CI-safe)
[group: 'testing']
test-skip-interactive:
    ./doxa_test -r --provider mock --skip-interactive

# Run VCR cassette replay tests
[group: 'testing']
test-vcr:
    uv run pytest tests/test_vcr_openai.py -v

# Regenerate pytest snapshot files
[group: 'testing']
update-snapshots:
    uv run pytest --snapshot-update

# ─── Versioning ───────────────────────────────────────────────────────

# Show current version from pyproject.toml
[group: 'versioning']
current-version:
    @grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'

# ─── Release ──────────────────────────────────────────────────────────
# Releases are automated via release-please. Land conventional commits on
# main; release-please opens a Release PR with the bumped version +
# CHANGELOG.md. Merging that PR tags `vX.Y.Z`, which triggers publish.yml
# to push to TestPyPI and PyPI via OIDC trusted publishing.

# Build distribution (wheel + sdist)
[group: 'release']
build:
    uv build

# Publish to TestPyPI (requires OIDC or UV_PUBLISH_TOKEN)
[group: 'release']
publish-test:
    uv publish --publish-url https://test.pypi.org/legacy/

# Publish to PyPI (requires OIDC or UV_PUBLISH_TOKEN)
[group: 'release']
publish:
    uv publish

# ─── Release (Manual / .pypirc fallback) ─────────────────────────────
# Fallback path for when OIDC trusted publishing is unavailable (CI down,
# hot-fix from laptop). Primary path is `just publish` / `just publish-test`.
# Requires `.pypirc` (copy from `.pypirc.template`, chmod 600) with API
# tokens. `uv publish` is used (not twine); tokens are parsed from `.pypirc`
# and passed via `--token`.

# Verify .pypirc exists and is locked to mode 0600
[group: 'release-manual']
check-pypirc:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ ! -f .pypirc ]; then
        echo "ERROR: .pypirc not found."
        echo ""
        echo "Create it from the template, then fill in your tokens:"
        echo "  cp .pypirc.template .pypirc"
        echo "  chmod 600 .pypirc"
        echo "  \$EDITOR .pypirc"
        echo ""
        echo "PyPI tokens:     https://pypi.org/manage/account/token/"
        echo "TestPyPI tokens: https://test.pypi.org/manage/account/token/"
        exit 1
    fi
    perms=$(stat -f '%A' .pypirc 2>/dev/null || stat -c '%a' .pypirc 2>/dev/null)
    if [ "$perms" != "600" ]; then
        echo "ERROR: .pypirc has insecure permissions ($perms)."
        echo "Run: chmod 600 .pypirc"
        exit 1
    fi

# Manually publish to PyPI using token from .pypirc (uv publish)
[group: 'release-manual']
publish-manual: check-pypirc
    #!/usr/bin/env bash
    set -euo pipefail
    token=$(python3 -c "import configparser,sys; c=configparser.ConfigParser(); c.read('.pypirc'); print(c['pypi']['password'])")
    if [ -z "$token" ] || [ "$token" = "REPLACE_WITH_PYPI_TOKEN" ]; then
        echo "ERROR: PyPI token not set in .pypirc [pypi] section."
        exit 1
    fi
    rm -rf dist/
    uv build
    uv publish --token "$token"

# Manually publish to TestPyPI using token from .pypirc (uv publish)
[group: 'release-manual']
publish-test-manual: check-pypirc
    #!/usr/bin/env bash
    set -euo pipefail
    token=$(python3 -c "import configparser,sys; c=configparser.ConfigParser(); c.read('.pypirc'); print(c['testpypi']['password'])")
    if [ -z "$token" ] || [ "$token" = "REPLACE_WITH_TESTPYPI_TOKEN" ]; then
        echo "ERROR: TestPyPI token not set in .pypirc [testpypi] section."
        exit 1
    fi
    rm -rf dist/
    uv build
    uv publish --publish-url https://test.pypi.org/legacy/ --token "$token"

# ─── Dev ──────────────────────────────────────────────────────────────

# Show doxa help
[group: 'dev']
dev:
    ./doxa --help

# Run example research prompt
[group: 'dev']
run:
    ./doxa "What is quantum computing?" --provider mock

# Initialize doxa configuration
[group: 'dev']
init:
    ./doxa init

# Quick smoke test of basic functionality
[group: 'dev']
smoke-test:
    ./doxa --version
    ./doxa --help

# ─── Virtual Environment ─────────────────────────────────────────────

# Create virtual environment
[group: 'venv']
venv:
    #!/usr/bin/env bash
    if [ ! -d ".venv" ]; then
        uv venv --python 3.11
        echo "Virtual environment created. Activate with: source .venv/bin/activate"
    else
        echo "Virtual environment already exists. Activate with: source .venv/bin/activate"
    fi

# Install package and dependencies into virtual environment
[group: 'venv']
venv-install: venv
    uv pip install -e ".[dev]"

# Sync exact dependencies
[group: 'venv']
venv-sync: venv
    uv sync

# Remove virtual environment
[group: 'venv']
venv-clean:
    rm -rf .venv

# ─── Git Hooks (lefthook) ────────────────────────────────────────────

# Install lefthook, gitleaks, and git hooks
[group: 'git-hooks']
install-lefthook: install-gitleaks
    @if command -v lefthook > /dev/null 2>&1; then \
        echo "lefthook is already installed"; \
    else \
        brew install lefthook; \
    fi
    lefthook install

# Install gitleaks (used by the pre-commit secret scan)
[group: 'git-hooks']
install-gitleaks:
    ./scripts/install-gitleaks.sh
