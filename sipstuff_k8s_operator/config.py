"""Operator configuration loaded from environment variables."""

import os
from dataclasses import dataclass


def _parse_bool(value: str) -> bool:
    """Parse a string into a boolean (truthy: ``"true"``, ``"1"``, ``"yes"``)."""
    return value.strip().lower() in ("true", "1", "yes")


@dataclass(frozen=True)
class OperatorConfig:
    """Immutable configuration for the SIP call job operator.

    All values are sourced from environment variables via :meth:`from_env`.
    """

    namespace: str
    job_image: str
    sip_secret_name: str
    job_ttl_seconds: int
    job_backoff_limit: int
    host_network: bool
    port: int

    @classmethod
    def from_env(cls) -> OperatorConfig:
        """Build an :class:`OperatorConfig` from environment variables.

        Environment variables
        ---------------------
        JOB_NAMESPACE : str, optional
            Kubernetes namespace for created jobs
            (default: value from ``/var/run/secrets/kubernetes.io/serviceaccount/namespace``
            or ``"sipstuff"``).
        JOB_IMAGE : str, optional
            Container image for SIP call jobs (default ``"xomoxcc/somestuff:latest"``).
        SIP_SECRET_NAME : str, optional
            Name of the K8s Secret containing default SIP credentials
            (default ``"sip-credentials"``).
        JOB_TTL_SECONDS : str, optional
            TTL in seconds after job completion before cleanup (default ``3600``).
        JOB_BACKOFF_LIMIT : str, optional
            Number of retries before marking job as failed (default ``0``).
        JOB_HOST_NETWORK : str, optional
            Whether to use host networking for SIP/RTP (default ``"true"``).
        PORT : str, optional
            HTTP listen port (default ``8080``).
        """
        default_namespace = "sipstuff"
        ns_file = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
        if os.path.isfile(ns_file):
            with open(ns_file) as fh:
                default_namespace = fh.read().strip() or default_namespace

        return cls(
            namespace=os.environ.get("JOB_NAMESPACE", default_namespace).strip(),
            job_image=os.environ.get("JOB_IMAGE", "xomoxcc/somestuff:latest").strip(),
            sip_secret_name=os.environ.get("SIP_SECRET_NAME", "sip-credentials").strip(),
            job_ttl_seconds=int(os.environ.get("JOB_TTL_SECONDS", "3600")),
            job_backoff_limit=int(os.environ.get("JOB_BACKOFF_LIMIT", "0")),
            host_network=_parse_bool(os.environ.get("JOB_HOST_NETWORK", "true")),
            port=int(os.environ.get("PORT", "8080")),
        )
