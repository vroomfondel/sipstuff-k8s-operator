"""FastAPI router with SIP call job endpoints."""

import json
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from kubernetes.client import BatchV1Api
from loguru import logger as glogger

from sipstuff_k8s_operator import __version__
from sipstuff_k8s_operator.job_builder import build_job
from sipstuff_k8s_operator.models import CallRequest, CallResponse, HealthResponse, JobInfo

if TYPE_CHECKING:
    from sipstuff_k8s_operator.config import OperatorConfig

logger = glogger.bind(classname="api")

router = APIRouter()


def _get_config(request: Request) -> OperatorConfig:
    return request.app.state.config  # type: ignore[no-any-return]


def _get_batch_api(request: Request) -> BatchV1Api:
    return request.app.state.batch_api


def _job_status(job: object) -> str:
    """Derive a human-readable status string from a V1Job object."""
    status = getattr(job, "status", None)
    if status is None:
        return "unknown"
    if getattr(status, "succeeded", None) and status.succeeded > 0:
        return "succeeded"
    if getattr(status, "failed", None) and status.failed > 0:
        return "failed"
    if getattr(status, "active", None) and status.active > 0:
        return "running"
    return "pending"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness / readiness probe."""
    return HealthResponse(status="ok", version=__version__)


@router.post("/call", response_model=CallResponse, status_code=201)
def create_call(body: CallRequest, request: Request) -> CallResponse:
    """Create a K8s Job that executes a SIP call."""
    config = _get_config(request)
    batch_api = _get_batch_api(request)

    job = build_job(body, config)
    logger.debug("Job spec:\n{}", json.dumps(job.to_dict(), indent=2, default=str))
    logger.info("Creating job {} in namespace {}", job.metadata.name, config.namespace)

    batch_api.create_namespaced_job(namespace=config.namespace, body=job)

    return CallResponse(job_name=job.metadata.name, namespace=config.namespace, status="created")


@router.get("/jobs", response_model=list[JobInfo])
def list_jobs(request: Request) -> list[JobInfo]:
    """List SIP call jobs."""
    config = _get_config(request)
    batch_api = _get_batch_api(request)

    result = batch_api.list_namespaced_job(
        namespace=config.namespace,
        label_selector="app=sipstuff-operator",
    )

    jobs: list[JobInfo] = []
    for item in result.items:
        jobs.append(
            JobInfo(
                name=item.metadata.name,
                namespace=item.metadata.namespace,
                status=_job_status(item),
                created_at=item.metadata.creation_timestamp,
                completed_at=getattr(item.status, "completion_time", None),
            )
        )
    return jobs


@router.get("/jobs/{job_name}", response_model=JobInfo)
def get_job(job_name: str, request: Request) -> JobInfo:
    """Get status of a specific SIP call job."""
    config = _get_config(request)
    batch_api = _get_batch_api(request)

    try:
        item = batch_api.read_namespaced_job(name=job_name, namespace=config.namespace)
    except Exception as exc:
        if "404" in str(exc) or "Not Found" in str(exc):
            raise HTTPException(status_code=404, detail=f"Job '{job_name}' not found") from exc
        raise

    return JobInfo(
        name=item.metadata.name,
        namespace=item.metadata.namespace,
        status=_job_status(item),
        created_at=item.metadata.creation_timestamp,
        completed_at=getattr(item.status, "completion_time", None),
    )
