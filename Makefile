.PHONY: help requirements test clean review build wheel sdist

VERSION := $(shell grep -m 1 version pyproject.toml | tr -s ' ' | tr -d '"' | tr -d "'" | cut -d' ' -f3)
PACKAGE := $(shell grep -m 1 name pyproject.toml | tr -s ' ' | tr -d '"' | tr -d "'" | cut -d' ' -f3)

OBJ := $(shell find rngit -type f)
OBJ += pyproject.toml
OBJ += README.md

ifeq ($(VENV_BIN_ACTIVATE),)
VENV_BIN_ACTIVATE := .venv/bin/activate
endif
define PLATFORM_SCRIPT
from sysconfig import get_platform
print(get_platform().replace('-', '_'), end="")
endef
export PLATFORM_SCRIPT
PLATFORM := $(shell python -c "$$PLATFORM_SCRIPT")

define ABI_SCRIPT
def main():
    try:
        from wheel.pep425tags import get_abi_tag
        print(get_abi_tag(), end="")
        return
    except ModuleNotFoundError:
        pass

    try:
        from wheel.vendored.packaging import tags
    except ModuleNotFoundError:
        from packaging import tags

    name=tags.interpreter_name()
    version=tags.interpreter_version()
    print(f"{name}{version}", end="")

main()
endef
export ABI_SCRIPT
ABI := $(shell python -c "$$ABI_SCRIPT")

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(VENV_BIN_ACTIVATE):
	python -m venv .venv
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install --upgrade pip; \
	python -m pip install --upgrade build wheel

requirements: $(VENV_BIN_ACTIVATE) ## Install development requirements
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install -e ".[dev]" -q

test: requirements ## Run tests
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pytest -v tests/

build: sdist wheel ## Build wheel and sdist

dist:
	mkdir -p dist

wheel: dist/git_remote_rns-${VERSION}-${ABI}-${ABI}-${PLATFORM}.whl # Build wheel

sdist: dist/git_remote_rns-${VERSION}.tar.gz # Build sdist

dist/git_remote_rns-${VERSION}-${ABI}-${ABI}-${PLATFORM}.whl: $(VENV_BIN_ACTIVATE) dist $(OBJ)
	@. ${VENV_BIN_ACTIVATE}; \
	python -m build --wheel

dist/git_remote_rns-${VERSION}.tar.gz: $(VENV_BIN_ACTIVATE) dist $(OBJ)
	@. ${VENV_BIN_ACTIVATE}; \
	python -m build --sdist

clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info/ .venv/
	rm -rf *.build *.dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

lint: requirements ## Lint the codebase
	@set -e;\
	. ${VENV_BIN_ACTIVATE}; \
	python -m basedpyright \
	  --warnings \
	  rngit \
	  tests; \
	python -m prospector \
	  --profile strictness_veryhigh \
	  --with-tool pyroma \
	  --with-tool vulture \
	  --with-tool bandit \
	  --with-tool pyright \
	  --with-tool ruff \
	  --without-tool pycodestyle \
	  rngit; \
	python -m prospector \
	  --profile strictness_veryhigh \
	  --with-tool pyroma \
	  --with-tool vulture \
	  --with-tool bandit \
	  --with-tool pyright \
	  --with-tool ruff \
	  --without-tool pycodestyle \
	  tests

review: ## Have coderabbit review the code
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
