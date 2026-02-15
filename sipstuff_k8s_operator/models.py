"""Pydantic v2 models for API request/response."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CallRequest(BaseModel):
    """POST /call request body."""

    dest: str
    text: str | None = None
    wav: str | None = None

    # SIP connection overrides (optional — fall back to operator secret)
    sip_server: str | None = None
    sip_port: int | None = Field(None, ge=1, le=65535)
    sip_user: str | None = None
    sip_password: str | None = None
    sip_transport: Literal["udp", "tcp", "tls"] | None = None
    sip_srtp: Literal["disabled", "optional", "mandatory"] | None = None
    sip_tls_verify: bool | None = None

    # NAT traversal overrides (optional — fall back to operator secret)
    stun_servers: str | None = None
    ice_enabled: bool | None = None
    turn_server: str | None = None
    turn_username: str | None = None
    turn_password: str | None = None
    turn_transport: Literal["udp", "tcp", "tls"] | None = None
    keepalive_sec: int | None = Field(None, ge=0, le=600)
    public_address: str | None = None

    # Call params
    timeout: int = Field(60, ge=1, le=600)
    pre_delay: float = Field(0.0, ge=0.0, le=30.0)
    post_delay: float = Field(0.0, ge=0.0, le=30.0)
    wait_for_silence: float | None = Field(None, ge=0.0, le=30.0)
    inter_delay: float = Field(0.0, ge=0.0, le=30.0)
    repeat: int = Field(1, ge=1, le=100)

    # TTS params
    tts_model: str | None = None
    tts_sample_rate: int | None = Field(None, ge=0, le=48000)
    tts_data_dir: str | None = None

    # STT params
    stt_model: str | None = None
    stt_language: str | None = None
    stt_data_dir: str | None = None

    # Recording — should point to a file below /data/recordings/ when the
    # RECORDING_DIR volume mount is configured, e.g. "/data/recordings/call.wav"
    record: str | None = None
    transcribe: bool = False

    # Job options
    verbose: bool = False

    @model_validator(mode="after")
    def _check_text_or_wav(self) -> "CallRequest":
        if not self.text and not self.wav:
            raise ValueError("Exactly one of 'text' or 'wav' must be provided")
        if self.text and self.wav:
            raise ValueError("Exactly one of 'text' or 'wav' must be provided, not both")
        return self


class CallResponse(BaseModel):
    """Response from POST /call."""

    job_name: str
    namespace: str
    status: str


class JobInfo(BaseModel):
    """Job status information."""

    name: str
    namespace: str
    status: str
    created_at: datetime | None = None
    completed_at: datetime | None = None


class HealthResponse(BaseModel):
    """Response from GET /health."""

    status: str
    version: str
