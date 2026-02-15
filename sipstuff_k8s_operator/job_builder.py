"""Build Kubernetes Job specs for SIP calls."""

import random
import string
from datetime import datetime, timezone

from kubernetes.client import (
    V1Container,
    V1EnvVar,
    V1EnvVarSource,
    V1HostPathVolumeSource,
    V1Job,
    V1JobSpec,
    V1ObjectMeta,
    V1PodSecurityContext,
    V1PodSpec,
    V1PodTemplateSpec,
    V1SecretKeySelector,
    V1SecurityContext,
    V1Volume,
    V1VolumeMount,
)

from sipstuff_k8s_operator.config import OperatorConfig
from sipstuff_k8s_operator.models import CallRequest

_PIPER_MOUNT_PATH = "/data/piper"
_WHISPER_MOUNT_PATH = "/data/whisper"
_RECORDING_MOUNT_PATH = "/data/recordings"


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
    """Construct a :class:`V1Job` for executing a SIP call.

    When ``piper_data_dir``, ``whisper_data_dir``, or ``recording_dir`` are set
    on *config*, corresponding ``hostPath`` volumes (``DirectoryOrCreate``) are
    created and mounted at ``/data/piper``, ``/data/whisper``, and
    ``/data/recordings`` respectively.  Container env vars point to the mount
    paths (not the host paths).

    When ``record`` is set on *request*, ``--record <path>`` is passed to the
    ``sipstuff.cli call`` command (the path should point inside the recording
    volume, e.g. ``/data/recordings/call.wav``).

    If any of ``run_as_user``, ``run_as_group``, or ``fs_group`` are set on
    *config*, a ``PodSecurityContext`` is added to the pod spec.
    """
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

    if request.stt_model:
        args.extend(["--stt-model", request.stt_model])
    if request.stt_language:
        args.extend(["--stt-language", request.stt_language])
    if request.stt_data_dir:
        args.extend(["--stt-data-dir", request.stt_data_dir])

    if request.transcribe:
        args.append("--transcribe")

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

    # Volume mounts for model cache and recording directories
    volumes: list[V1Volume] = []
    volume_mounts: list[V1VolumeMount] = []

    _dir_mappings: list[tuple[str | None, str, str, str]] = [
        (config.piper_data_dir, "piper-data", _PIPER_MOUNT_PATH, "PIPER_DATA_DIR"),
        (config.whisper_data_dir, "whisper-data", _WHISPER_MOUNT_PATH, "WHISPER_DATA_DIR"),
        (config.recording_dir, "recording-data", _RECORDING_MOUNT_PATH, "RECORDING_DIR"),
    ]

    for host_path, vol_name, mount_path, env_name in _dir_mappings:
        if host_path is not None:
            volumes.append(
                V1Volume(name=vol_name, host_path=V1HostPathVolumeSource(path=host_path, type="DirectoryOrCreate"))
            )
            volume_mounts.append(V1VolumeMount(name=vol_name, mount_path=mount_path))
            env_vars.append(V1EnvVar(name=env_name, value=mount_path))

    # CLI arg for recording (per-call file path inside the recording volume)
    if request.record is not None:
        args.extend(["--record", request.record])

    job_name = _generate_job_name()

    container = V1Container(
        name="sip-caller",
        image=config.job_image,
        image_pull_policy="Always",
        command=args,
        env=env_vars,
        volume_mounts=volume_mounts or None,
    )

    # Pod security context (optional, from operator config)
    security_context: V1PodSecurityContext | None = None
    if config.run_as_user is not None or config.run_as_group is not None or config.fs_group is not None:
        security_context = V1PodSecurityContext(
            run_as_user=config.run_as_user,
            run_as_group=config.run_as_group,
            fs_group=config.fs_group,
        )

    # initContainer to fix hostPath ownership (fsGroup does not apply to hostPath volumes)
    init_containers: list[V1Container] | None = None
    if volume_mounts and config.run_as_user is not None:
        owner = str(config.run_as_user)
        if config.fs_group is not None:
            owner += f":{config.fs_group}"
        elif config.run_as_group is not None:
            owner += f":{config.run_as_group}"
        dirs = " ".join(vm.mount_path for vm in volume_mounts)
        init_containers = [
            V1Container(
                name="fix-permissions",
                image="busybox:latest",
                command=["sh", "-c", f"chown -R {owner} {dirs}"],
                volume_mounts=list(volume_mounts),
                security_context=V1SecurityContext(run_as_user=0),
            )
        ]

    pod_spec = V1PodSpec(
        containers=[container],
        init_containers=init_containers,
        restart_policy="Never",
        host_network=config.host_network,
        volumes=volumes or None,
        security_context=security_context,
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
