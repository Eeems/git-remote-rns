SHELL := bash

VERSION := $(shell grep -m 1 version pyproject.toml | tr -s ' ' | tr -d '"' | tr -d "'" | cut -d' ' -f3)
PACKAGE := $(shell grep -m 1 name pyproject.toml | tr -s ' ' | tr -d '"' | tr -d "'" | cut -d' ' -f3)

OBJ := $(shell find rngit -type f)
OBJ += pyproject.toml
OBJ += README.md

ifndef FUZZ_TIMEOUT
FUZZ_TIMEOUT := 60
endif

ifndef SKIP_TESTS
TESTS := $(shell find tests -type f -name '*.py')
INDIVIDUAL_TESTS := $(shell SKIP_TESTS=1 MAKEFLAGS= make --no-print-directory list-tests)
endif

FUZZERS := $(shell find fuzz -maxdepth 1 -type f -name '*.py')

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

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(VENV_BIN_ACTIVATE):
	python -m venv .venv
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install --upgrade pip; \
	python -m pip install --upgrade build wheel

.PHONY: requirements
requirements: $(VENV_BIN_ACTIVATE) pyproject.toml ## Install requirements
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install \
	  --quiet \
	  --editable \
	  .

.PHONY: requirements-web
requirements-web: $(VENV_BIN_ACTIVATE) pyproject.toml ## Install web requirements
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install \
	  --quiet \
	  --editable \
	  ".[web]"

.PHONY: requirements-dev
requirements-dev: $(VENV_BIN_ACTIVATE) pyproject.toml ## Install dev requirements
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install \
	  --quiet \
	  --editable \
	  ".[dev]"

.PHONY: requirements-test
requirements-test: requirements-web $(VENV_BIN_ACTIVATE) pyproject.toml ## Install test requirements
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install \
	  --quiet \
	  --editable \
	  ".[test]"

.PHONY: requirements-fuzz
requirements-fuzz: requirements-web $(VENV_BIN_ACTIVATE) pyproject.toml ## Install fuzz requirements
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install \
	  --quiet \
	  --editable \
	  ".[fuzz]"

.PHONY: test
test: requirements-test ## Run tests
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pytest \
	  -vv \
	  tests/

.PHONY: fuzz
fuzz: $(FUZZERS) ## Run fuzz tests

.repos:
	mkdir -p .repos

.PHONY: test-web
test-web: .repos requirements-web ## Run rngit-web for testing
	@cd .repos;\
	if [ ! -d git-remote-rns ];then \
	  git clone https://github.com/Eeems/git-remote-rns; \
	fi
	@cd .repos;\
	if [ ! -d empty ];then \
	  mkdir empty; \
	  cd empty; \
	  git init; \
	fi
	@cd .repos;\
	if [ ! -d empty.git ];then \
	  mkdir empty.git; \
	  cd empty.git; \
	  git init --bare; \
	fi
	@. ${VENV_BIN_ACTIVATE}; \
	python -m rngit \
	  rngit-web \
	  --verbose \
	  --allow-debug 1b72330713792d8fb086e881c52c684c \
	  .repos

.PHONY: test-server
test-server: .repos requirements-web ## Run rngit node with the web server for testing
	@cd .repos;\
	if [ ! -d git-remote-rns ];then \
	  git clone https://github.com/Eeems/git-remote-rns; \
	fi
	@cd .repos;\
	if [ ! -d empty ];then \
	  mkdir empty; \
	  cd empty; \
	  git init; \
	fi
	@cd .repos;\
	if [ ! -d empty.git ];then \
	  mkdir empty.git; \
	  cd empty.git; \
	  git init --bare; \
	fi
	@. ${VENV_BIN_ACTIVATE}; \
	python -m rngit \
	  rngit \
	  --verbose \
	  --nomadnet \
	  --allow-read 1b72330713792d8fb086e881c52c684c \
	  --allow-write 4bbc9219ce924a7d77e00584523c2d4e \
	  .repos

.PHONY: list-tests
list-tests: ## List all available tests
	@if [ ! -f ${VENV_BIN_ACTIVATE} ];then \
	  $(MAKE) requirements-test >/dev/null; \
	fi
	@. ${VENV_BIN_ACTIVATE}; \
	if ! python -m pytest --version >/dev/null;then \
	  $(MAKE) requirements-test >/dev/null; \
	fi
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pytest \
	  --collect-only \
	  --quiet \
	  --disable-warnings \
	  tests/ \
	| grep -v ' tests collected in ' \
	| xargs -n1

.PHONY: list-fuzzers
list-fuzzers:
	@echo $(FUZZERS) | xargs -n1

ifndef SKIP_TESTS
define test-target
.PHONY: $2
$2: requirements-test
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pytest \
	  -vv \
	  $1
endef

$(foreach T,\
	$(TESTS),\
	$(eval $(call test-target,\
		$(T),\
		$(shell echo $(T) | sed 's|:|\\:|g'),\,\
	))\
)

$(foreach T,\
	$(INDIVIDUAL_TESTS),\
	$(eval $(call \
		test-target,\
		$(T),\
		$(shell echo $(T) | sed 's|:|\\:|g'),\
	))\
)
endif
define fuzz-target
.PHONY: $1
$1: requirements-fuzz
	@. $${VENV_BIN_ACTIVATE}; \
	cd fuzz; \
	python $2 \
	  -rss_limit_mb=2048 \
	  -max_total_time=$$(FUZZ_TIMEOUT)
endef
$(foreach T,\
	$(FUZZERS),\
	$(eval $(call fuzz-target,\
		$(T),\
		$(shell basename $(T)),\
	))\
)

.PHONY: build
build: sdist wheel ## Build wheel and sdist

dist:
	mkdir -p dist

.PHONY: wheel
wheel: dist/git_remote_rns-${VERSION}-${ABI}-${ABI}-${PLATFORM}.whl # Build wheel

.PHONY: sdist
sdist: dist/git_remote_rns-${VERSION}.tar.gz # Build sdist

dist/git_remote_rns-${VERSION}-${ABI}-${ABI}-${PLATFORM}.whl: $(VENV_BIN_ACTIVATE) dist $(OBJ)
	@. ${VENV_BIN_ACTIVATE}; \
	python -m build --wheel

dist/git_remote_rns-${VERSION}.tar.gz: $(VENV_BIN_ACTIVATE) dist $(OBJ)
	@. ${VENV_BIN_ACTIVATE}; \
	python -m build --sdist

.PHONY: clean
clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info/ .venv/
	rm -rf *.build *.dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

.PHONY: whitelist
whitelist: requirements-dev ## Generate lint whitelists
	@set -e;\
	. ${VENV_BIN_ACTIVATE}; \
	rm -f rngit/__whitelist.py; \
	python -m vulture --make-whitelist rngit/ >rngit/__whitelist.py || true; \
	rm -f tests/__whitelist.py; \
	python -m vulture --make-whitelist tests/ >tests/__whitelist.py || true


.PHONY: lint
lint: requirements-dev requirements-web requirements-test requirements-fuzz ## Lint the codebase
	@set -e;\
	. ${VENV_BIN_ACTIVATE}; \
	runtool() { \
	  tool=$$1; \
	  shift; \
	  echo -n "Running $$tool: "; \
	  set +e; \
	  output=$$(python -um "$$tool" $$@ 2>&1); \
	  ret=$$?; \
	  set -e; \
	  if [[ $$ret -ne 0 ]];then \
	    echo "FAIL ($$ret)"; \
	    echo "$$output"; \
	    exit $$ret; \
	  fi; \
	  echo "OKAY"; \
	}; \
	runtool pylint --recursive=yes .; \
	runtool ruff check; \
	for dir in rngit tests;do \
	  for tool in basedpyright vulture;do \
	    runtool "$$tool" "$$dir"; \
	  done; \
	done; \
	runtool bandit --recursive --configfile pyproject.toml .; \
	runtool dodgy --zero-exit --ignore-paths dist/ build/ .venv/ .repos/; \
	runtool pyroma .

.PHONY: review
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
