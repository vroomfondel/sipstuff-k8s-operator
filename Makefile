.PHONY: tests help install venv lint isort tcheck build commit-checks prepare gitleaks update-all-dockerhub-readmes
SHELL := /usr/bin/bash
.ONESHELL:


help:
	@printf "\ninstall\n\tinstall requirements\n"
	@printf "\nisort\n\tmake isort import corrections\n"
	@printf "\nlint\n\tmake linter check with black\n"
	@printf "\ntcheck\n\tmake static type checks with mypy\n"
	@printf "\ntests\n\tLaunch tests\n"
	@printf "\nprepare\n\tLaunch tests and commit-checks\n"
	@printf "\ncommit-checks\n\trun pre-commit checks on all files\n"
	@printf "\nbuild\n\tbuild docker image\n"
	@printf "\nupdate-all-dockerhub-readmes \n\tupdate Docker Hub repo description from DOCKERHUB_OVERVIEW.md\n"


# check for "CI" not in os.environ || "GITHUB_RUN_ID" not in os.environ
venv_activated=if [ -z $${VIRTUAL_ENV+x} ] && [ -z $${GITHUB_RUN_ID+x} ] ; then printf "activating venv...\n" ; source .venv/bin/activate ; else printf "venv already activated or GITHUB_RUN_ID=$${GITHUB_RUN_ID} is set\n"; fi

install: venv

venv: .venv/touchfile

.venv/touchfile: requirements.txt requirements-dev.txt requirements-build.txt
	@if [ -z "$${GITHUB_RUN_ID}" ]; then \
		test -d .venv || python3.14 -m venv .venv; \
		source .venv/bin/activate; \
		pip install -r requirements-build.txt; \
		touch .venv/touchfile; \
	else \
		echo "Skipping venv setup because GITHUB_RUN_ID is set"; \
	fi


tests: venv
	@$(venv_activated)
	pytest .

lint: venv
	@$(venv_activated)
	black -l 120 .

isort: venv
	@$(venv_activated)
	isort .

tcheck: venv
	@$(venv_activated)
	mypy .

gitleaks: venv .git/hooks/pre-commit
	@$(venv_activated)
	pre-commit run gitleaks --all-files

.git/hooks/pre-commit: venv
	@$(venv_activated)
	pre-commit install

commit-checks: .git/hooks/pre-commit
	@$(venv_activated)
	pre-commit run --all-files

prepare: tests commit-checks

DOCKER_IMAGE := sipstuff-k8s-operator

build:
	docker build -t $(DOCKER_IMAGE):latest .

update-all-dockerhub-readmes:
	@AUTH=$$(jq -r '.auths["https://index.docker.io/v1/"].auth' ~/.docker/config.json | base64 -d) && \
	USERNAME=$$(echo "$$AUTH" | cut -d: -f1) && \
	PASSWORD=$$(echo "$$AUTH" | cut -d: -f2-) && \
	TOKEN=$$(curl -s -X POST https://hub.docker.com/v2/users/login/ \
	  -H "Content-Type: application/json" \
	  -d '{"username":"'"$$USERNAME"'","password":"'"$$PASSWORD"'"}' \
	  | jq -r .token) && \
	for mapping in \
	  ".:xomoxcc/sipstuff-k8s-operator"; do \
	  DIR=$$(echo "$$mapping" | cut -d: -f1) && \
	  REPO=$$(echo "$$mapping" | cut -d: -f2) && \
	  FILE="$$DIR/DOCKERHUB_OVERVIEW.md" && \
	  if [ -f "$$FILE" ]; then \
	    echo "Updating $$REPO from $$FILE..." && \
	    curl -s -X PATCH "https://hub.docker.com/v2/repositories/$$REPO/" \
	      -H "Authorization: Bearer $$TOKEN" \
	      -H "Content-Type: application/json" \
	      -d "{\"full_description\": $$(jq -Rs . "$$FILE")}" \
	      | jq -r '.full_description | length | "  Updated: \(.) chars"'; \
	  else \
	    echo "Skipping $$REPO - $$FILE not found"; \
	  fi; \
	done