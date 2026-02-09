"""FastAPI application factory for the SIP call job operator."""

from __future__ import annotations

from fastapi import FastAPI
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from loguru import logger as glogger

from sipstuff_k8s_operator.api import router
from sipstuff_k8s_operator.config import OperatorConfig

logger = glogger.bind(classname="operator")


def _init_k8s() -> None:
    """Load Kubernetes configuration (in-cluster with local fallback)."""
    try:
        k8s_config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes config")
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
        logger.info("Loaded local kubeconfig")


def create_app(config: OperatorConfig) -> FastAPI:
    """Build and return the FastAPI application."""
    _init_k8s()

    app = FastAPI(title="sipstuff-k8s-operator", version="1.0.0")
    app.state.config = config
    app.state.batch_api = k8s_client.BatchV1Api()

    app.include_router(router)

    return app
