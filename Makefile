.PHONY: help install dev test clean review build

VENV_BIN_FOLDER := $(shell python -c "import sys; print('Scripts' if sys.platform == 'win32' else 'bin')")
VENV_ACTIVATE := .venv/$(VENV_BIN_FOLDER)/activate

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(VENV_ACTIVATE):
	python -m venv .venv

install-deps: $(VENV_ACTIVATE)
	@source ${VENV_ACTIVATE}; \
	python -m pip install -e . -q

install-dev: $(VENV_ACTIVATE)
	@source ${VENV_ACTIVATE}; \
	python -m pip install -e ".[dev]" -q

install: install-deps

dev: install-dev

test: install-dev
	@source ${VENV_ACTIVATE}; \
	python -m pytest -v tests/

build: install-dev ## Build wheel with Nuitka
	@source ${VENV_ACTIVATE}; \
	python -m build --wheel

clean:
	rm -rf build/ dist/ *.egg-info/ .venv/
	rm -rf *.build *.dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

lint: install-dev
	@set -e;\
	source ${VENV_ACTIVATE}; \
	python -m basedpyright \
	  --warnings \
	  rnsremote \
	  tests; \
	python -m prospector \
	  --profile strictness_veryhigh \
	  --with-tool pyroma \
	  --with-tool vulture \
	  --with-tool bandit \
	  --with-tool pyright \
	  --with-tool ruff \
	  --without-tool pycodestyle \
	  rnsremote; \
	python -m prospector \
	  --profile strictness_veryhigh \
	  --with-tool pyroma \
	  --with-tool vulture \
	  --with-tool bandit \
	  --with-tool pyright \
	  --with-tool ruff \
	  --without-tool pycodestyle \
	  tests

review:
	@if command -v coderabbit >/dev/null 2>&1; then \
	  output=$$(coderabbit review --prompt-only 2>&1); \
	  status=$$?; \
	  if echo "$$output" | grep -qiE "auth|unauthorized|login|401"; then \
	    echo "coderabbit auth required"; \
	  elif [ $$status -ne 0 ]; then \
	    echo "$$output"; \
	    echo "coderabbit review failed with exit code $$status"; \
	  fi; \
	else \
	  echo "coderabbit not installed"; \
	fi
