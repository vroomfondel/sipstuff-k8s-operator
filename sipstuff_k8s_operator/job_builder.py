"""Build Kubernetes Job specs for SIP calls."""

import random
import string
from datetime import datetime, timezone

from kubernetes.client import (
    V1Container,
    V1EnvVar,
    V1EnvVarSource,
    V1Job,
    V1JobSpec,
    V1ObjectMeta,
    V1PodSpec,
    V1PodTemplateSpec,
    V1SecretKeySelector,
)

from sipstuff_k8s_operator.config import OperatorConfig
from sipstuff_k8s_operator.models import CallRequest


def _generate_job_name() -> str:
    """Generate a unique job name like ``sipcall-20260208-1430-a7f3``."""
    now = datetime.now(timezone.utc)
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"sipcall-{now.strftime('%Y%m%d-%H%M')}-{suffix}"


def _secret_env(name: str, secret_name: str, key: str) -> V1EnvVar:
    """Create an env var sourced from a K8s Secret key."""
    return V1EnvVar(
        name=name,
        value_from=V1EnvVarSource(secret_key_ref=V1SecretKeySelector(name=secret_name, key=key, optional=True)),
    )


def build_job(request: CallRequest, config: OperatorConfig) -> V1Job:
    """Construct a :class:`V1Job` for executing a SIP call."""
    args: list[str] = ["python3", "-m", "sipstuff.cli", "call", "--dest", request.dest]

    if request.text:
        args.extend(["--text", request.text])
    if request.wav:
        args.extend(["--wav", request.wav])

    args.extend(["--timeout", str(request.timeout)])

    if request.pre_delay > 0:
        args.extend(["--pre-delay", str(request.pre_delay)])
    if request.inter_delay > 0:
        args.extend(["--inter-delay", str(request.inter_delay)])
    if request.post_delay > 0:
        args.extend(["--post-delay", str(request.post_delay)])
    if request.repeat > 1:
        args.extend(["--repeat", str(request.repeat)])

    if request.tts_model:
        args.extend(["--tts-model", request.tts_model])
    if request.tts_sample_rate is not None:
        args.extend(["--tts-sample-rate", str(request.tts_sample_rate)])
    if request.tts_data_dir:
        args.extend(["--tts-data-dir", request.tts_data_dir])

    if request.verbose:
        args.append("--verbose")

    # Environment variables for SIP connection
    env_vars: list[V1EnvVar] = []

    # For each SIP param: use request override if provided, otherwise reference the secret
    sip_fields = [
        ("SIP_SERVER", "sip_server", "SIP_SERVER"),
        ("SIP_PORT", "sip_port", "SIP_PORT"),
        ("SIP_USER", "sip_user", "SIP_USER"),
        ("SIP_PASSWORD", "sip_password", "SIP_PASSWORD"),
        ("SIP_TRANSPORT", "sip_transport", "SIP_TRANSPORT"),
        ("SIP_SRTP", "sip_srtp", "SIP_SRTP"),
    ]

    for env_name, attr_name, secret_key in sip_fields:
        val = getattr(request, attr_name)
        if val is not None:
            env_vars.append(V1EnvVar(name=env_name, value=str(val)))
        else:
            env_vars.append(_secret_env(env_name, config.sip_secret_name, secret_key))

    # SIP TLS verification (bool → "true"/"false" string)
    if request.sip_tls_verify is not None:
        env_vars.append(V1EnvVar(name="SIP_TLS_VERIFY_SERVER", value=str(request.sip_tls_verify).lower()))
    else:
        env_vars.append(_secret_env("SIP_TLS_VERIFY_SERVER", config.sip_secret_name, "SIP_TLS_VERIFY_SERVER"))

    # NAT traversal env vars (same secret-fallback pattern)
    nat_fields: list[tuple[str, str, str]] = [
        ("SIP_STUN_SERVERS", "stun_servers", "SIP_STUN_SERVERS"),
        ("SIP_ICE_ENABLED", "ice_enabled", "SIP_ICE_ENABLED"),
        ("SIP_TURN_SERVER", "turn_server", "SIP_TURN_SERVER"),
        ("SIP_TURN_USERNAME", "turn_username", "SIP_TURN_USERNAME"),
        ("SIP_TURN_PASSWORD", "turn_password", "SIP_TURN_PASSWORD"),
        ("SIP_TURN_TRANSPORT", "turn_transport", "SIP_TURN_TRANSPORT"),
        ("SIP_KEEPALIVE_SEC", "keepalive_sec", "SIP_KEEPALIVE_SEC"),
        ("SIP_PUBLIC_ADDRESS", "public_address", "SIP_PUBLIC_ADDRESS"),
    ]

    for env_name, attr_name, secret_key in nat_fields:
        val = getattr(request, attr_name)
        if val is not None:
            if isinstance(val, bool):
                env_vars.append(V1EnvVar(name=env_name, value=str(val).lower()))
            else:
                env_vars.append(V1EnvVar(name=env_name, value=str(val)))
        else:
            env_vars.append(_secret_env(env_name, config.sip_secret_name, secret_key))

    # When turn_server is provided, implicitly enable TURN
    if request.turn_server is not None:
        env_vars.append(V1EnvVar(name="SIP_TURN_ENABLED", value="true"))

    job_name = _generate_job_name()

    container = V1Container(
        name="sip-caller",
        image=config.job_image,
        command=args,
        env=env_vars,
    )

    pod_spec = V1PodSpec(
        containers=[container],
        restart_policy="Never",
        host_network=config.host_network,
    )

    template = V1PodTemplateSpec(
        metadata=V1ObjectMeta(labels={"app": "sipstuff-operator", "component": "sip-caller"}),
        spec=pod_spec,
    )

    job_spec = V1JobSpec(
        template=template,
        backoff_limit=config.job_backoff_limit,
        ttl_seconds_after_finished=config.job_ttl_seconds,
    )

    return V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=V1ObjectMeta(
            name=job_name,
            namespace=config.namespace,
            labels={"app": "sipstuff-operator", "component": "sip-caller"},
        ),
        spec=job_spec,
    )
