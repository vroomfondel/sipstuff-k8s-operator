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
```

Run a single test:
```bash
source .venv/bin/activate && pytest tests/test_base.py::test_version_exists -v
```

Run the operator locally:
```bash
python -m sipstuff_k8s_operator           # Run operator (starts uvicorn on :8080)
python -m sipstuff_k8s_operator conntest  # Test K8s API connectivity only
```

## Architecture

### Package: `sipstuff_k8s_operator/`

- `__init__.py` — Version (0.0.1), loguru configuration with `classname` extra field
- `__main__.py` — Entry point, startup banner, uvicorn server, `conntest` subcommand for K8s API check
- `config.py` — `OperatorConfig` frozen dataclass, all config from env vars (namespace, job_image, sip_secret_name, etc.)
- `models.py` — Pydantic v2 models: `CallRequest` (incl. SIP overrides, NAT traversal fields), `CallResponse`, `JobInfo`, `HealthResponse`
- `job_builder.py` — `build_job()` constructs K8s `V1Job` from `CallRequest` + `OperatorConfig`, with CLI args for `sipstuff.cli call` and env vars (SIP, TLS, NAT/TURN) from request overrides or K8s Secret
- `api.py` — FastAPI router with `POST /call`, `GET /jobs`, `GET /jobs/{name}`, `GET /health`
- `operator.py` — `create_app()` factory: initializes K8s client (in-cluster with local fallback), wires config + BatchV1Api into FastAPI app state

### SIP Configuration

Per-request SIP overrides are supported via the `CallRequest` body. If not provided, values fall back to a K8s Secret (default name: `sip-credentials`) via `secretKeyRef`. Supported env vars: `SIP_SERVER`, `SIP_PORT`, `SIP_USER`, `SIP_PASSWORD`, `SIP_TRANSPORT`, `SIP_SRTP`, `SIP_TLS_VERIFY_SERVER`. NAT traversal env vars: `SIP_STUN_SERVERS`, `SIP_ICE_ENABLED`, `SIP_TURN_SERVER`, `SIP_TURN_USERNAME`, `SIP_TURN_PASSWORD`, `SIP_TURN_TRANSPORT`, `SIP_KEEPALIVE_SEC`, `SIP_PUBLIC_ADDRESS`. When `turn_server` is set, `SIP_TURN_ENABLED=true` is added automatically.

### K8s Manifests (`k8s/`)

- `namespace.yaml` — Namespace `sipstuff`
- `rbac.yaml` — ServiceAccount, Role (batch/jobs + pods), RoleBinding
- `secret.yaml.example` — Example Secret with SIP credential keys
- `deployment.yaml` — Operator Deployment (1 replica, port 8080, health probes)
- `service.yaml` — ClusterIP Service (80 → 8080)

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

- `initial_setup_github_dockerhub.sh` — One-time setup: creates DockerHub repo + OAT, GitHub repo, gist for clone badges, and sets GitHub Actions secrets. Validates that the OAT has push+pull access to the target DH repo via `check_dockerhub_token.py --json`; if not, offers to create a new OAT. Also ensures the DH repo exists (unauthenticated check first, authenticated fallback for private repos).
- `check_dockerhub_token.py` — Checks Docker Hub token permissions (pull/push/delete) per repo via registry auth endpoint. Supports `--json` output. Used by the setup script for token validation.
- `dh_login.py` — Docker Hub login helper with MFA/TOTP support, prints JWT to stdout.
- `build-container-multiarch.sh` — Multi-arch Docker image build script.
- `update_badge.py` — Updates clone count badge gist.
- `include.sh` / `include.local.sh` — Shell variable config (credentials, repo names). `include.local.sh` is gitignored and holds secrets.

## Code Style

- **Line length**: 120 characters (Black)
- **Type checking**: MyPy strict mode (`disallow_untyped_defs`, `check_untyped_defs`), tests excluded from mypy
- **Logging**: Loguru with `classname` extra binding per module/class, level via `LOGURU_LEVEL` env var
- **Python version**: 3.14
- **Import sorting**: isort with black profile
