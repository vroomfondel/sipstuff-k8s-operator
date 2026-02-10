[![mypy and pytests](https://github.com/vroomfondel/sipstuff-k8s-operator/actions/workflows/mypynpytests.yml/badge.svg)](https://github.com/vroomfondel/sipstuff-k8s-operator/actions/workflows/mypynpytests.yml)
[![BuildAndPushMultiarch](https://github.com/vroomfondel/sipstuff-k8s-operator/actions/workflows/buildmultiarchandpush.yml/badge.svg)](https://github.com/vroomfondel/sipstuff-k8s-operator/actions/workflows/buildmultiarchandpush.yml)
[![black-lint](https://github.com/vroomfondel/sipstuff-k8s-operator/actions/workflows/checkblack.yml/badge.svg)](https://github.com/vroomfondel/sipstuff-k8s-operator/actions/workflows/checkblack.yml)
![Cumulative Clones](https://img.shields.io/endpoint?logo=github&url=https://gist.githubusercontent.com/vroomfondel/69048d146ee747668673b2a8f8ebc81e/raw/sipstuff-k8s-operator_clone_count.json)
[![Docker Pulls](https://img.shields.io/docker/pulls/xomoxcc/sipstuff-k8s-operator?logo=docker)](https://hub.docker.com/r/xomoxcc/sipstuff-k8s-operator/tags)
[![PyPI](https://img.shields.io/pypi/v/sipstuff-k8s-operator?logo=pypi&logoColor=white)](https://pypi.org/project/sipstuff-k8s-operator/)

[![Gemini_Generated_Image_23m8jo23m8jo23m8_250x250.png](https://raw.githubusercontent.com/vroomfondel/sipstuff-k8s-operator/main/Gemini_Generated_Image_23m8jo23m8jo23m8_250x250.png)](https://hub.docker.com/r/xomoxcc/sipstuff-k8s-operator/tags)

# WIP !!!!!

# sipstuff-k8s-operator

A Kubernetes operator that exposes a FastAPI HTTP API for creating SIP call Jobs. It accepts call requests via `POST /call`, builds Kubernetes Jobs that run `sipstuff.cli call`, and tracks job status.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/call` | Create a K8s Job that executes a SIP call |
| `GET` | `/jobs` | List all SIP call jobs |
| `GET` | `/jobs/{name}` | Get status of a specific job |
| `GET` | `/health` | Liveness / readiness probe |

## Quick Start

```bash
# Install dependencies (creates a Python 3.14 venv)
make install

# Run the operator locally (starts uvicorn on :8080)
python -m sipstuff_k8s_operator

# Test K8s API connectivity only
python -m sipstuff_k8s_operator conntest
```

Create a call:

```bash
curl -X POST http://localhost:8080/call \
  -H "Content-Type: application/json" \
  -d '{"dest": "+4912345678", "text": "Hello from sipstuff"}'
```

List jobs:

```bash
curl http://localhost:8080/jobs
```

Check health:

```bash
curl http://localhost:8080/health
```

## Configuration

All settings are read from environment variables. Every variable is optional with sensible defaults.

| Variable | Default | Description |
|---|---|---|
| `JOB_NAMESPACE` | Downward API namespace or `"sipstuff"` | K8s namespace for created jobs |
| `JOB_IMAGE` | `"xomoxcc/somestuff:latest"` | Container image for SIP call jobs |
| `SIP_SECRET_NAME` | `"sip-credentials"` | K8s Secret name for default SIP credentials |
| `JOB_TTL_SECONDS` | `3600` | TTL in seconds after job completion before cleanup |
| `JOB_BACKOFF_LIMIT` | `0` | Number of retries before marking a job as failed |
| `JOB_HOST_NETWORK` | `"true"` | Use host networking for SIP/RTP |
| `PORT` | `8080` | HTTP listen port |

## Kubernetes Deployment

Manifests are provided in the `k8s/` directory:

```bash
# Create namespace
kubectl apply -f k8s/namespace.yaml

# Create RBAC (ServiceAccount, Role, RoleBinding)
kubectl apply -f k8s/rbac.yaml

# Create the SIP credentials secret (copy and edit the example first)
cp k8s/secret.yaml.example k8s/secret.yaml
# edit k8s/secret.yaml with your SIP credentials
kubectl apply -f k8s/secret.yaml

# Deploy the operator
kubectl apply -f k8s/deployment.yaml

# Expose via ClusterIP service (80 -> 8080)
kubectl apply -f k8s/service.yaml
```

The operator Deployment uses liveness and readiness probes against `/health`, runs as a single replica on port 8080, and uses a dedicated `sipstuff-operator` ServiceAccount with RBAC permissions for batch/jobs and pods.

## Call Request Body

`POST /call` accepts a JSON body with the following fields. Exactly one of `text` or `wav` must be provided.

| Field | Type | Default | Description |
|---|---|---|---|
| `dest` | `string` | *(required)* | Destination phone number or SIP URI |
| `text` | `string` | `null` | Text to speak via TTS (mutually exclusive with `wav`) |
| `wav` | `string` | `null` | Path to a WAV file to play (mutually exclusive with `text`) |
| `sip_server` | `string` | `null` | SIP server override |
| `sip_port` | `integer` | `null` | SIP port override (1-65535) |
| `sip_user` | `string` | `null` | SIP username override |
| `sip_password` | `string` | `null` | SIP password override |
| `sip_transport` | `string` | `null` | Transport protocol: `"udp"`, `"tcp"`, or `"tls"` |
| `sip_srtp` | `string` | `null` | SRTP mode: `"disabled"`, `"optional"`, or `"mandatory"` |
| `sip_tls_verify` | `boolean` | `null` | TLS server certificate verification |
| `stun_servers` | `string` | `null` | Comma-separated STUN servers |
| `ice_enabled` | `boolean` | `null` | Enable ICE for NAT traversal |
| `turn_server` | `string` | `null` | TURN relay server (host:port) |
| `turn_username` | `string` | `null` | TURN auth username |
| `turn_password` | `string` | `null` | TURN auth password |
| `turn_transport` | `string` | `null` | TURN transport: `"udp"`, `"tcp"`, or `"tls"` |
| `keepalive_sec` | `integer` | `null` | UDP keepalive interval in seconds (0-600) |
| `public_address` | `string` | `null` | Public IP address for SDP/Contact headers |
| `timeout` | `integer` | `60` | Call timeout in seconds (1-600) |
| `pre_delay` | `float` | `0.0` | Delay before call in seconds (0-30) |
| `inter_delay` | `float` | `0.0` | Delay between WAV repeats in seconds (0-30) |
| `post_delay` | `float` | `0.0` | Delay after call in seconds (0-30) |
| `repeat` | `integer` | `1` | Number of call repetitions (1-100) |
| `tts_model` | `string` | `null` | TTS model name |
| `tts_sample_rate` | `integer` | `null` | TTS sample rate in Hz (0-48000) |
| `tts_data_dir` | `string` | `null` | TTS data directory |
| `verbose` | `boolean` | `false` | Enable verbose logging in the call job |

## SIP Credentials

SIP connection parameters can be provided per-request in the call body (`sip_server`, `sip_port`, `sip_user`, `sip_password`, `sip_transport`, `sip_srtp`, `sip_tls_verify`). When a field is not provided, the operator falls back to the K8s Secret specified by `SIP_SECRET_NAME` (default: `sip-credentials`).

The secret should contain these keys:

```yaml
stringData:
  SIP_SERVER: "sip.example.com"
  SIP_PORT: "5060"
  SIP_USER: "sipuser"
  SIP_PASSWORD: "changeme"
  SIP_TRANSPORT: "udp"
  SIP_SRTP: "disabled"
  # SIP_TLS_VERIFY_SERVER: "false"
```

See `k8s/secret.yaml.example` for a complete example.

### NAT Traversal

NAT traversal settings (STUN, ICE, TURN, keepalive, public address) can be provided per-request or configured globally via the same K8s Secret. Add any of these optional keys to the `sip-credentials` Secret:

```yaml
stringData:
  # SIP_STUN_SERVERS: "stun.l.google.com:19302"
  # SIP_ICE_ENABLED: "false"
  # SIP_TURN_SERVER: "turn.example.com:3478"
  # SIP_TURN_USERNAME: ""
  # SIP_TURN_PASSWORD: ""
  # SIP_TURN_TRANSPORT: "udp"
  # SIP_KEEPALIVE_SEC: "0"
  # SIP_PUBLIC_ADDRESS: ""
```

When `turn_server` is provided in a request, `SIP_TURN_ENABLED=true` is automatically set on the job.

## Development

```bash
make lint           # Black formatter (line length: 120)
make isort          # Sort imports
make tcheck         # MyPy type checking (strict mode)
make tests          # Run pytest
make prepare        # Run tests + commit-checks
make commit-checks  # Run all pre-commit hooks
make gitleaks       # Scan for secrets
```

Run a single test:

```bash
source .venv/bin/activate && pytest tests/test_base.py::test_version_exists -v
```

## Initial Setup (GitHub + Docker Hub)

The `repo_scripts/initial_setup_github_dockerhub.sh` script automates first-time project setup:

1. Creates the Docker Hub repository (if no OAT exists yet)
2. Creates a scoped Organization Access Token (OAT) with push+pull for the target repo
3. **Validates** that the OAT actually has push+pull access via `check_dockerhub_token.py`
   - If permissions are missing, offers to create a new OAT with the correct scopes
4. Ensures the Docker Hub repo exists (handles both public and private repos)
5. Creates a GitHub repo and a public gist for clone tracking badges
6. Sets all required GitHub Actions secrets (`DOCKERHUB_TOKEN`, `DOCKERHUB_USERNAME`, `GIST_ID`, `GIST_TOKEN`, `REPO_PRIV_TOKEN`)

Configuration is read from `repo_scripts/include.local.sh` (not checked in). See `repo_scripts/include.sh` for the variable template.

```bash
# Run the setup
./repo_scripts/initial_setup_github_dockerhub.sh
```

## Docker

```bash
# Build the image
make build
# or
docker build -t sipstuff-k8s-operator:latest .

# Run locally
docker run --rm -p 8080:8080 sipstuff-k8s-operator:latest
```

The image is based on `python:3.14-slim` and runs `python3 -m sipstuff_k8s_operator` as its entrypoint.

## License
This project is licensed under the LGPL where applicable/possible — see [LICENSE.md](LICENSE.md). Some files/parts may use other licenses: [MIT](LICENSEMIT.md) | [GPL](LICENSEGPL.md) | [LGPL](LICENSELGPL.md). Always check per‑file headers/comments.


## Authors
- Repo owner (primary author)
- Additional attributions are noted inline in code comments


## Acknowledgments
- Inspirations and snippets are referenced in code comments where appropriate.


## ⚠️ Note

This is a development/experimental project. For production use, review security settings, customize configurations, and test thoroughly in your environment. Provided "as is" without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose and noninfringement. In no event shall the authors or copyright holders be liable for any claim, damages or other liability, whether in an action of contract, tort or otherwise, arising from, out of or in connection with the software or the use or other dealings in the software. Use at your own risk.

