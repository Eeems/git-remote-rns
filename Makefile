SHELL := bash

VERSION := $(shell grep -m 1 version pyproject.toml | tr -s ' ' | tr -d '"' | tr -d "'" | cut -d' ' -f3)
PACKAGE := $(shell grep -m 1 name pyproject.toml | tr -s ' ' | tr -d '"' | tr -d "'" | cut -d' ' -f3 | tr '-' '_')

OBJ := $(shell find rngit -type f)
OBJ += pyproject.toml
OBJ += README.md

ifndef FUZZ_TIMEOUT
FUZZ_TIMEOUT := 60
endif
FUZZERS := $(shell find fuzz -maxdepth 1 -type f -name '*.py')

ifeq ($(VENV_BIN_ACTIVATE),)
VENV_BIN_ACTIVATE := .venv/bin/activate
endif

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(VENV_BIN_ACTIVATE):
	python -m venv .venv
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install --upgrade pip; \
	python -m pip install --upgrade build wheel

.PHONY: requirements-fuzz
requirements-fuzz: $(VENV_BIN_ACTIVATE) pyproject.toml ## Install fuzz requirements
	@. ${VENV_BIN_ACTIVATE}; \
	python -m pip install \
	  --quiet \
	  --editable \
	  ".[fuzz]"

.PHONY: fuzz
fuzz: $(FUZZERS) ## Run fuzz tests

.repos:
	mkdir -p .repos

.PHONY: list-fuzzers
list-fuzzers: ## List all available fuzzers
	@echo $(FUZZERS) | xargs -n1

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

.PHONY: clean
clean: ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info/ .venv/ wheelhouse/
	rm -rf *.build *.dist
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
