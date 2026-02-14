# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**sipstuff-k8s-operator** (`sipstuff_k8s_operator`) is a Kubernetes operator that exposes a FastAPI HTTP API for creating SIP call Jobs. It accepts call requests via `POST /call`, builds Kubernetes Job specs that run `xomoxcc/somestuff` with the `sipstuff.cli call` entrypoint, and tracks job status.

API endpoints: `POST /call`, `GET /jobs`, `GET /jobs/{name}`, `GET /health`.

## Build and Development Commands

```bash
make install              # Create Python 3.14 venv and install deps
make lint                 # Black formatter (line length: 120)
make isort                # Sort imports
make tcheck               # MyPy type checking (strict mode)
make tests                # Run pytest
make prepare              # Run tests + commit-checks
make commit-checks        # Run all pre-commit hooks
make gitleaks             # Scan for secrets
make build                # Build Docker image
make pypibuild            # Build package for PyPI
make pypipush             # Publish package to PyPI
```

Run a single test:
```bash
source .venv/bin/activate && pytest tests/test_base.py::test_version_exists -v
```

Run the operator locally:
```bash
python -m sipstuff_k8s_operator                          # Run operator (starts uvicorn on :8080)
python -m sipstuff_k8s_operator conntest                 # Test K8s API connectivity only
python -m sipstuff_k8s_operator dumpjob [--json] [IMAGE] # Print sample Job spec as YAML (or JSON)
```

## Architecture

### Package: `sipstuff_k8s_operator/`

- `__init__.py` — Version string, `configure_logging()` with loguru `classname` extra field. Logging is disabled by default; `__main__.py` enables it.
- `__main__.py` — Entry point with subcommands (`conntest`, `dumpjob`), startup banner, uvicorn server. Also has `_strip_none()` utility for cleaning dict output.
- `config.py` — `OperatorConfig` frozen dataclass, all config from env vars (namespace, job_image, sip_secret_name, etc.)
- `models.py` — Pydantic v2 models: `CallRequest` (incl. SIP overrides, NAT traversal fields), `CallResponse`, `JobInfo`, `HealthResponse`
- `job_builder.py` — `build_job()` constructs K8s `V1Job` from `CallRequest` + `OperatorConfig`, with CLI args for `sipstuff.cli call` and env vars (SIP, TLS, NAT/TURN) from request overrides or K8s Secret
- `api.py` — FastAPI router with all endpoints
- `operator.py` — `create_app()` factory: initializes K8s client (in-cluster with local fallback), wires config + BatchV1Api into FastAPI app state

### Request → Job Flow

`POST /call` → `api.create_call()` → `job_builder.build_job(CallRequest, OperatorConfig)` → K8s `V1Job`. Per-request SIP overrides become plain env vars on the container; missing fields fall back to `secretKeyRef` from the K8s Secret (`sip-credentials` by default). When `turn_server` is set, `SIP_TURN_ENABLED=true` is added automatically.

### K8s Manifests (`k8s/`)

Namespace, RBAC (ServiceAccount + Role + RoleBinding), Secret example, Deployment (1 replica, port 8080, health probes), ClusterIP Service (80 → 8080).

### Environment Variables

All optional with sensible defaults:
- `JOB_NAMESPACE` — K8s namespace for jobs (default: from downward API or `"sipstuff"`)
- `JOB_IMAGE` — Container image for call jobs (default: `"xomoxcc/somestuff:latest"`)
- `SIP_SECRET_NAME` — K8s Secret name for SIP credentials (default: `"sip-credentials"`)
- `JOB_TTL_SECONDS` — Job cleanup TTL (default: `3600`)
- `JOB_BACKOFF_LIMIT` — Job retry limit (default: `0`)
- `JOB_HOST_NETWORK` — Use host networking for SIP/RTP (default: `"true"`)
- `PORT` — HTTP listen port (default: `8080`)

### Repo Scripts (`repo_scripts/`)

- `initial_setup_github_dockerhub.sh` — One-time setup: creates DockerHub repo + OAT, GitHub repo, gist for clone badges, and sets GitHub Actions secrets. Validates OAT permissions via `check_dockerhub_token.py --json`.
- `check_dockerhub_token.py` — Checks Docker Hub token permissions (pull/push/delete) per repo. Supports `--json` output.
- `dh_login.py` — Docker Hub login helper with MFA/TOTP support, prints JWT to stdout.
- `build-container-multiarch.sh` — Multi-arch Docker image build script.
- `update_badge.py` — Updates clone count badge gist.
- `include.sh` / `include.local.sh` — Shell variable config. `include.local.sh` is gitignored and holds secrets.

## Code Style

- **Line length**: 120 characters (Black)
- **Type checking**: MyPy strict mode with `pydantic.mypy` plugin. Tests ARE type-checked (not excluded). The `kubernetes` module has `ignore_missing_imports = true` override.
- **Logging**: Loguru with `classname` extra binding per module/class, level via `LOGURU_LEVEL` env var (default: `DEBUG`)
- **Python version**: 3.14
- **Import sorting**: isort with black profile

## Dependencies

Three-tier structure: `requirements.txt` (runtime) → `requirements-dev.txt` (adds dev tools: pre-commit, mypy, pytest, black, isort, type stubs) → `requirements-build.txt` (adds hatch). Runtime deps also declared in `pyproject.toml`.

## Pre-commit Hooks

Configured in `.pre-commit-config.yaml` with `fail_fast: true`:
1. `check-yaml` (with `--allow-multiple-documents`)
2. `black` (diff + check, line length 120)
3. `mypy` (local hook using `language: system` — runs from project venv, NOT isolated)
4. `gitleaks` (secret detection)
