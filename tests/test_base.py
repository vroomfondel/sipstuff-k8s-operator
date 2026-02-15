"""Tests for sipstuff_k8s_operator."""

import os
from unittest.mock import patch

import pytest

import sipstuff_k8s_operator
from sipstuff_k8s_operator.config import OperatorConfig
from sipstuff_k8s_operator.models import CallRequest, CallResponse, HealthResponse, JobInfo

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def test_version_exists() -> None:
    """Verify that the package has a version string."""
    assert hasattr(sipstuff_k8s_operator, "__version__")
    assert isinstance(sipstuff_k8s_operator.__version__, str)
    assert len(sipstuff_k8s_operator.__version__) > 0


def test_version_format() -> None:
    """Verify version follows semver pattern."""
    version = sipstuff_k8s_operator.__version__
    parts = version.split(".")
    assert len(parts) >= 2, "Version should have at least major.minor"
    for part in parts:
        assert part.isdigit() or part[0].isdigit(), f"Version part '{part}' should start with a digit"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_defaults() -> None:
    """Config loads with all defaults when no env vars are set."""
    with patch.dict(os.environ, {}, clear=True):
        cfg = OperatorConfig.from_env()
    assert cfg.namespace == "sipstuff"
    assert cfg.job_image == "xomoxcc/somestuff:latest"
    assert cfg.sip_secret_name == "sip-credentials"
    assert cfg.job_ttl_seconds == 3600
    assert cfg.job_backoff_limit == 0
    assert cfg.host_network is True
    assert cfg.port == 8080


def test_config_from_env() -> None:
    """Config picks up custom env vars."""
    env = {
        "JOB_NAMESPACE": "custom-ns",
        "JOB_IMAGE": "myregistry/myimage:v2",
        "SIP_SECRET_NAME": "my-sip-secret",
        "JOB_TTL_SECONDS": "7200",
        "JOB_BACKOFF_LIMIT": "3",
        "JOB_HOST_NETWORK": "false",
        "PORT": "9090",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = OperatorConfig.from_env()
    assert cfg.namespace == "custom-ns"
    assert cfg.job_image == "myregistry/myimage:v2"
    assert cfg.sip_secret_name == "my-sip-secret"
    assert cfg.job_ttl_seconds == 7200
    assert cfg.job_backoff_limit == 3
    assert cfg.host_network is False
    assert cfg.port == 9090


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_call_request_text() -> None:
    """CallRequest with text is valid."""
    req = CallRequest(dest="+4912345", text="Hello world")
    assert req.dest == "+4912345"
    assert req.text == "Hello world"
    assert req.wav is None


def test_call_request_wav() -> None:
    """CallRequest with wav is valid."""
    req = CallRequest(dest="+4912345", wav="/audio/test.wav")
    assert req.wav == "/audio/test.wav"
    assert req.text is None


def test_call_request_neither_text_nor_wav() -> None:
    """CallRequest without text or wav fails validation."""
    with pytest.raises(ValueError, match="text.*wav"):
        CallRequest(dest="+4912345")


def test_call_request_both_text_and_wav() -> None:
    """CallRequest with both text and wav fails validation."""
    with pytest.raises(ValueError, match="text.*wav"):
        CallRequest(dest="+4912345", text="Hello", wav="/audio/test.wav")


def test_call_request_sip_overrides() -> None:
    """CallRequest accepts SIP connection overrides."""
    req = CallRequest(
        dest="+4912345",
        text="Hello",
        sip_server="sip.example.com",
        sip_port=5061,
        sip_user="testuser",
        sip_password="secret",
        sip_transport="tls",
        sip_srtp="mandatory",
    )
    assert req.sip_server == "sip.example.com"
    assert req.sip_port == 5061
    assert req.sip_transport == "tls"
    assert req.sip_srtp == "mandatory"


def test_call_request_inter_delay() -> None:
    """CallRequest inter_delay has correct default and constraint."""
    req = CallRequest(dest="+49123", text="hi")
    assert req.inter_delay == 0.0

    req2 = CallRequest(dest="+49123", text="hi", inter_delay=5.0)
    assert req2.inter_delay == 5.0

    with pytest.raises(ValueError):
        CallRequest(dest="+49123", text="hi", inter_delay=31.0)
    with pytest.raises(ValueError):
        CallRequest(dest="+49123", text="hi", inter_delay=-1.0)


def test_call_request_nat_overrides() -> None:
    """CallRequest accepts NAT traversal fields."""
    req = CallRequest(
        dest="+49123",
        text="hi",
        sip_tls_verify=False,
        stun_servers="stun.l.google.com:19302",
        ice_enabled=True,
        turn_server="turn.example.com:3478",
        turn_username="user",
        turn_password="pass",
        turn_transport="udp",
        keepalive_sec=30,
        public_address="203.0.113.5",
    )
    assert req.sip_tls_verify is False
    assert req.stun_servers == "stun.l.google.com:19302"
    assert req.ice_enabled is True
    assert req.turn_server == "turn.example.com:3478"
    assert req.turn_username == "user"
    assert req.turn_password == "pass"
    assert req.turn_transport == "udp"
    assert req.keepalive_sec == 30
    assert req.public_address == "203.0.113.5"


def test_call_request_field_constraints() -> None:
    """CallRequest enforces field constraints."""
    with pytest.raises(ValueError):
        CallRequest(dest="+49123", text="hi", timeout=0)
    with pytest.raises(ValueError):
        CallRequest(dest="+49123", text="hi", sip_port=70000)
    with pytest.raises(ValueError):
        CallRequest(dest="+49123", text="hi", repeat=0)


def test_call_response() -> None:
    """CallResponse round-trips correctly."""
    resp = CallResponse(job_name="sipcall-20260208-1430-ab12", namespace="sipstuff", status="created")
    assert resp.job_name == "sipcall-20260208-1430-ab12"


def test_health_response() -> None:
    """HealthResponse round-trips correctly."""
    resp = HealthResponse(status="ok", version="1.0.0")
    assert resp.status == "ok"


def test_job_info() -> None:
    """JobInfo round-trips correctly."""
    info = JobInfo(name="test-job", namespace="sipstuff", status="running")
    assert info.name == "test-job"
    assert info.created_at is None


# ---------------------------------------------------------------------------
# Job builder
# ---------------------------------------------------------------------------


def test_job_builder_text() -> None:
    """Job builder produces a valid V1Job for a text-based call."""
    from sipstuff_k8s_operator.job_builder import build_job

    cfg = OperatorConfig(
        namespace="test-ns",
        job_image="myimage:latest",
        sip_secret_name="my-secret",
        job_ttl_seconds=1800,
        job_backoff_limit=1,
        host_network=True,
        port=8080,
        piper_data_dir=None,
        whisper_data_dir=None,
        recording_dir=None,
        run_as_user=None,
        run_as_group=None,
        fs_group=None,
    )
    req = CallRequest(dest="+4912345", text="Hello")
    job = build_job(req, cfg)

    assert job.metadata.name.startswith("sipcall-")
    assert job.metadata.namespace == "test-ns"
    assert job.metadata.labels["app"] == "sipstuff-operator"
    assert job.spec.backoff_limit == 1
    assert job.spec.ttl_seconds_after_finished == 1800

    container = job.spec.template.spec.containers[0]
    assert container.image == "myimage:latest"
    assert "--dest" in container.command
    assert "+4912345" in container.command
    assert "--text" in container.command
    assert "Hello" in container.command
    assert job.spec.template.spec.host_network is True
    assert job.spec.template.spec.restart_policy == "Never"


def test_job_builder_sip_override() -> None:
    """When SIP params are provided in the request, they become plain env vars."""
    from sipstuff_k8s_operator.job_builder import build_job

    cfg = OperatorConfig(
        namespace="ns",
        job_image="img:latest",
        sip_secret_name="secret",
        job_ttl_seconds=3600,
        job_backoff_limit=0,
        host_network=False,
        port=8080,
        piper_data_dir=None,
        whisper_data_dir=None,
        recording_dir=None,
        run_as_user=None,
        run_as_group=None,
        fs_group=None,
    )
    req = CallRequest(dest="+49123", text="test", sip_server="sip.example.com", sip_port=5061)
    job = build_job(req, cfg)

    container = job.spec.template.spec.containers[0]
    env_dict = {e.name: e for e in container.env}

    # Overridden fields should be plain values
    assert env_dict["SIP_SERVER"].value == "sip.example.com"
    assert env_dict["SIP_PORT"].value == "5061"

    # Non-overridden fields should reference the secret
    assert env_dict["SIP_USER"].value_from is not None
    assert env_dict["SIP_USER"].value_from.secret_key_ref.name == "secret"


def test_job_builder_wav() -> None:
    """Job builder produces correct args for wav-based calls."""
    from sipstuff_k8s_operator.job_builder import build_job

    cfg = OperatorConfig(
        namespace="ns",
        job_image="img:latest",
        sip_secret_name="secret",
        job_ttl_seconds=3600,
        job_backoff_limit=0,
        host_network=False,
        port=8080,
        piper_data_dir=None,
        whisper_data_dir=None,
        recording_dir=None,
        run_as_user=None,
        run_as_group=None,
        fs_group=None,
    )
    req = CallRequest(dest="+49123", wav="/audio/greeting.wav", verbose=True, repeat=3, pre_delay=2.0)
    job = build_job(req, cfg)

    cmd = job.spec.template.spec.containers[0].command
    assert "--wav" in cmd
    assert "/audio/greeting.wav" in cmd
    assert "--verbose" in cmd
    assert "--repeat" in cmd
    assert "3" in cmd
    assert "--pre-delay" in cmd
    assert "--text" not in cmd


def test_job_builder_inter_delay() -> None:
    """Job builder adds --inter-delay when inter_delay > 0."""
    from sipstuff_k8s_operator.job_builder import build_job

    cfg = OperatorConfig(
        namespace="ns",
        job_image="img:latest",
        sip_secret_name="secret",
        job_ttl_seconds=3600,
        job_backoff_limit=0,
        host_network=False,
        port=8080,
        piper_data_dir=None,
        whisper_data_dir=None,
        recording_dir=None,
        run_as_user=None,
        run_as_group=None,
        fs_group=None,
    )
    req = CallRequest(dest="+49123", text="test", inter_delay=2.5)
    job = build_job(req, cfg)

    cmd = job.spec.template.spec.containers[0].command
    assert "--inter-delay" in cmd
    assert "2.5" in cmd

    # When 0, flag should not appear
    req_zero = CallRequest(dest="+49123", text="test", inter_delay=0.0)
    job_zero = build_job(req_zero, cfg)
    cmd_zero = job_zero.spec.template.spec.containers[0].command
    assert "--inter-delay" not in cmd_zero


def test_job_builder_tls_verify() -> None:
    """Job builder sets SIP_TLS_VERIFY_SERVER env var from sip_tls_verify."""
    from sipstuff_k8s_operator.job_builder import build_job

    cfg = OperatorConfig(
        namespace="ns",
        job_image="img:latest",
        sip_secret_name="secret",
        job_ttl_seconds=3600,
        job_backoff_limit=0,
        host_network=False,
        port=8080,
        piper_data_dir=None,
        whisper_data_dir=None,
        recording_dir=None,
        run_as_user=None,
        run_as_group=None,
        fs_group=None,
    )
    req = CallRequest(dest="+49123", text="test", sip_tls_verify=False)
    job = build_job(req, cfg)

    env_dict = {e.name: e for e in job.spec.template.spec.containers[0].env}
    assert env_dict["SIP_TLS_VERIFY_SERVER"].value == "false"

    # When not provided, should fall back to secret
    req_none = CallRequest(dest="+49123", text="test")
    job_none = build_job(req_none, cfg)
    env_dict_none = {e.name: e for e in job_none.spec.template.spec.containers[0].env}
    assert env_dict_none["SIP_TLS_VERIFY_SERVER"].value_from is not None
    assert env_dict_none["SIP_TLS_VERIFY_SERVER"].value_from.secret_key_ref.name == "secret"


def test_job_builder_nat_fields() -> None:
    """Job builder sets NAT env vars correctly from request fields."""
    from sipstuff_k8s_operator.job_builder import build_job

    cfg = OperatorConfig(
        namespace="ns",
        job_image="img:latest",
        sip_secret_name="secret",
        job_ttl_seconds=3600,
        job_backoff_limit=0,
        host_network=False,
        port=8080,
        piper_data_dir=None,
        whisper_data_dir=None,
        recording_dir=None,
        run_as_user=None,
        run_as_group=None,
        fs_group=None,
    )
    req = CallRequest(
        dest="+49123",
        text="test",
        stun_servers="stun.l.google.com:19302",
        ice_enabled=True,
        turn_server="turn.example.com:3478",
        turn_username="user",
        turn_password="pass",
        turn_transport="tcp",
        keepalive_sec=15,
        public_address="203.0.113.5",
    )
    job = build_job(req, cfg)

    env_dict = {e.name: e for e in job.spec.template.spec.containers[0].env}
    assert env_dict["SIP_STUN_SERVERS"].value == "stun.l.google.com:19302"
    assert env_dict["SIP_ICE_ENABLED"].value == "true"
    assert env_dict["SIP_TURN_SERVER"].value == "turn.example.com:3478"
    assert env_dict["SIP_TURN_USERNAME"].value == "user"
    assert env_dict["SIP_TURN_PASSWORD"].value == "pass"
    assert env_dict["SIP_TURN_TRANSPORT"].value == "tcp"
    assert env_dict["SIP_KEEPALIVE_SEC"].value == "15"
    assert env_dict["SIP_PUBLIC_ADDRESS"].value == "203.0.113.5"
    # turn_server provided → SIP_TURN_ENABLED=true
    assert env_dict["SIP_TURN_ENABLED"].value == "true"

    # When NAT fields are not provided, they should fall back to secret
    req_none = CallRequest(dest="+49123", text="test")
    job_none = build_job(req_none, cfg)
    env_dict_none = {e.name: e for e in job_none.spec.template.spec.containers[0].env}
    assert env_dict_none["SIP_STUN_SERVERS"].value_from is not None
    assert env_dict_none["SIP_STUN_SERVERS"].value_from.secret_key_ref.name == "secret"
    # SIP_TURN_ENABLED should NOT be present when turn_server is not provided
    assert "SIP_TURN_ENABLED" not in env_dict_none
