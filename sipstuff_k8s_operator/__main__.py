"""Entry point for the sipstuff-k8s-operator.

Provides CLI subcommands (``conntest``, ``dumpjob``, ``startjob``) and the
default ``main`` path that launches the FastAPI/uvicorn HTTP server.

Examples:
    $ python -m sipstuff_k8s_operator dumpjob \
        --fs-group 1201 --run-as-user 1200 --run-as-group 1201 \
        --piper-data-dir=/mnt/nfs/sipstuff_shared/piper_data \
        --whisper-data-dir=/mnt/nfs/sipstuff_shared/whisper_data \
        --recording-dir=/mnt/nfs/sipstuff_shared/recordings \
        --sip-server 192.168.123.123 --sip-port 5060 \
        --sip-transport udp --sip-srtp disabled \
        --sip-user sipuser --sip-password sippassword \
        --inter-delay 2.0 --pre-delay 3.0 --post-delay 1.0 \
        --repeat 3 \
        --text "Houston, wir haben ein Problem." \
        --dest 192.168.123.123 \
        --record /data/recordings/recording_$(date +%Y%m%d_%H%M%S).wav
"""

import argparse
import logging
import sys
from dataclasses import fields
from typing import TYPE_CHECKING, Callable

import uvicorn
from loguru import logger as glogger
from tabulate import tabulate

from sipstuff_k8s_operator import __version__, configure_logging
from sipstuff_k8s_operator.config import OperatorConfig

if TYPE_CHECKING:
    from kubernetes.client import V1Job

configure_logging()
glogger.enable("sipstuff_k8s_operator")

_uvicorn_logger = glogger.bind(classname="uvicorn")


class _LoguruInterceptHandler(logging.Handler):
    """Intercept stdlib logging records and route them through loguru.

    Installed as the sole handler on uvicorn's loggers so that all uvicorn
    output is unified under the loguru sink.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Forward a stdlib ``LogRecord`` to the loguru logger.

        Args:
            record: The stdlib log record to forward.
        """
        level: str | int
        try:
            level = glogger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        _uvicorn_logger.opt(depth=6, exception=record.exc_info).log(level, "{}", record.getMessage())


UVICORN_LOG_CONFIG: dict[str, object] = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "loguru": {
            "()": lambda: _LoguruInterceptHandler(),
        },
    },
    "loggers": {
        "uvicorn": {
            "handlers": ["loguru"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.error": {
            "handlers": ["loguru"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": ["loguru"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


def _print_banner() -> None:
    """Log the operator startup banner with version and project links."""
    startup_rows = [
        ["version", __version__],
        ["github", "https://github.com/vroomfondel/sipstuff-k8s-operator"],
        ["Docker Hub", "https://hub.docker.com/r/xomoxcc/sipstuff-k8s-operator"],
    ]
    table_str = tabulate(startup_rows, tablefmt="mixed_grid")
    lines = table_str.split("\n")
    table_width = len(lines[0])
    title = "sipstuff-k8s-operator starting up"
    title_border = "\u250d" + "\u2501" * (table_width - 2) + "\u2511"
    title_row = "\u2502 " + title.center(table_width - 4) + " \u2502"
    separator = lines[0].replace("\u250d", "\u251d").replace("\u2511", "\u2525").replace("\u252f", "\u253f")

    glogger.opt(raw=True).info(
        "\n{}\n", title_border + "\n" + title_row + "\n" + separator + "\n" + "\n".join(lines[1:])
    )


def _print_config(cfg: OperatorConfig) -> None:
    """Log the active operator configuration as a formatted table.

    Args:
        cfg: The resolved operator configuration to display.
    """
    config_table = [[f.name, getattr(cfg, f.name)] for f in fields(cfg)]
    cfg_table_str = tabulate(config_table, tablefmt="mixed_grid")
    cfg_lines = cfg_table_str.split("\n")
    cfg_width = len(cfg_lines[0])
    cfg_title = "configuration"
    cfg_title_border = "\u250d" + "\u2501" * (cfg_width - 2) + "\u2511"
    cfg_title_row = "\u2502 " + cfg_title.center(cfg_width - 4) + " \u2502"
    cfg_separator = cfg_lines[0].replace("\u250d", "\u251d").replace("\u2511", "\u2525").replace("\u252f", "\u253f")

    glogger.opt(raw=True).info(
        "\n{}\n",
        cfg_title_border + "\n" + cfg_title_row + "\n" + cfg_separator + "\n" + "\n".join(cfg_lines[1:]),
    )


def _strip_none(obj: object) -> object:
    """Recursively remove keys with ``None`` values from nested dicts.

    Args:
        obj: A dict, list, or scalar value to clean.

    Returns:
        A copy of *obj* with all ``None``-valued dict entries removed at every
        nesting level.
    """
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(i) for i in obj]
    return obj


def _is_bool_field(annotation: type | None) -> bool:
    """Check whether a Pydantic field annotation resolves to ``bool``.

    Handles plain ``bool`` as well as ``Union`` / ``X | Y`` types that
    include ``bool`` among their arguments.

    Args:
        annotation: The type annotation from a Pydantic ``FieldInfo``.

    Returns:
        ``True`` if the annotation is or contains ``bool``.
    """
    import types
    import typing

    if annotation is None:
        return False
    if annotation is bool:
        return True
    origin = typing.get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        return any(a is bool for a in typing.get_args(annotation) if a is not type(None))
    return False


def _build_job_from_args(
    args: list[str], prog: str, extra_args_fn: "Callable[[argparse.ArgumentParser], None] | None" = None
) -> "tuple[argparse.Namespace, V1Job, OperatorConfig]":
    """Parse CLI args and build a Kubernetes ``V1Job`` with its config.

    Shared logic for the ``dumpjob`` and ``startjob`` subcommands.  CLI flags
    are auto-generated from ``CallRequest`` model fields; an optional
    ``--data`` JSON payload is merged with those flags (CLI wins).

    Args:
        args: Raw CLI argument strings to parse.
        prog: Program name shown in ``--help`` output.
        extra_args_fn: Optional callback that receives the
            ``ArgumentParser`` to register subcommand-specific flags before
            parsing.

    Returns:
        A tuple of ``(parsed_namespace, v1_job, operator_config)``.

    Raises:
        SystemExit: If argument parsing or Pydantic validation fails.
    """
    from dataclasses import replace

    from pydantic import ValidationError

    from sipstuff_k8s_operator.job_builder import build_job
    from sipstuff_k8s_operator.models import CallRequest

    parser = argparse.ArgumentParser(prog=prog, description="Build a K8s Job spec from CallRequest parameters")
    parser.add_argument("image", nargs="?", default=None, metavar="IMAGE", help="Override job container image")
    parser.add_argument("-d", "--data", default=None, help="JSON string matching the CallRequest schema")
    parser.add_argument("--piper-data-dir", default=None, help="Override host path for Piper TTS model cache")
    parser.add_argument("--whisper-data-dir", default=None, help="Override host path for Whisper STT model cache")
    parser.add_argument("--recording-dir", default=None, help="Override host path for recording files")
    parser.add_argument("--run-as-user", default=None, type=int, help="UID to run the job container as")
    parser.add_argument("--run-as-group", default=None, type=int, help="GID to run the job container as")
    parser.add_argument("--fs-group", default=None, type=int, help="fsGroup for the job pod security context")

    if extra_args_fn is not None:
        extra_args_fn(parser)

    # Auto-generate flags from CallRequest model fields — all as strings,
    # Pydantic handles type coercion and validation.
    for name, field_info in CallRequest.model_fields.items():
        cli_flag = f"--{name.replace('_', '-')}"
        if _is_bool_field(field_info.annotation):
            parser.add_argument(cli_flag, action="store_true", default=None, help=f"Set {name}")
        else:
            parser.add_argument(cli_flag, default=None, help=f"Set {name}")

    parsed = parser.parse_args(args)

    # Build merged dict: validate --data through Pydantic, then overlay CLI flags
    merged: dict[str, str | bool | dict[str, str]] = {}
    if parsed.data is not None:
        try:
            base = CallRequest.model_validate_json(parsed.data)
        except ValidationError as exc:
            parser.error(f"--data JSON validation failed:\n{exc}")
        merged.update(base.model_dump(exclude_unset=True))

    for name in CallRequest.model_fields:
        cli_val: str | bool | None = getattr(parsed, name, None)
        if cli_val is not None:
            merged[name] = cli_val

    # Convert --node-selector string to dict (e.g. "key=val,k2=v2" → dict)
    if "node_selector" in merged and isinstance(merged["node_selector"], str):
        from sipstuff_k8s_operator.config import parse_node_selector

        merged["node_selector"] = parse_node_selector(str(merged["node_selector"]))

    # Fall back to minimal defaults if nothing provided
    if "dest" not in merged:
        merged["dest"] = "+4912345"
    if "text" not in merged and "wav" not in merged:
        merged["text"] = "Hello world"

    try:
        req = CallRequest.model_validate(merged)
    except ValidationError as exc:
        parser.error(f"CallRequest validation failed:\n{exc}")

    cfg = OperatorConfig.from_env()
    if parsed.image:
        cfg = replace(cfg, job_image=parsed.image)
    if getattr(parsed, "piper_data_dir", None):
        cfg = replace(cfg, piper_data_dir=parsed.piper_data_dir)
    if getattr(parsed, "whisper_data_dir", None):
        cfg = replace(cfg, whisper_data_dir=parsed.whisper_data_dir)
    if getattr(parsed, "recording_dir", None):
        cfg = replace(cfg, recording_dir=parsed.recording_dir)
    if getattr(parsed, "run_as_user", None) is not None:
        cfg = replace(cfg, run_as_user=parsed.run_as_user)
    if getattr(parsed, "run_as_group", None) is not None:
        cfg = replace(cfg, run_as_group=parsed.run_as_group)
    if getattr(parsed, "fs_group", None) is not None:
        cfg = replace(cfg, fs_group=parsed.fs_group)

    job = build_job(req, cfg)
    return parsed, job, cfg


def dumpjob(args: list[str]) -> None:
    """Print a Kubernetes Job spec as YAML (default) or JSON without submitting it.

    Args:
        args: CLI arguments forwarded from the ``dumpjob`` subcommand.
    """
    import json as json_mod

    def _add_output_flag(parser: argparse.ArgumentParser) -> None:
        """Register the ``--json-output`` flag on *parser*.

        Args:
            parser: The argument parser to extend.
        """
        parser.add_argument("--json-output", action="store_true", help="Output JSON instead of YAML")

    parsed, job, _cfg = _build_job_from_args(args, prog="sipstuff-operator dumpjob", extra_args_fn=_add_output_flag)
    data = _strip_none(job.to_dict())

    if parsed.json_output:
        print(json_mod.dumps(data, indent=2, default=str))
    else:
        import yaml

        print(yaml.dump(data, default_flow_style=False, sort_keys=False))


def startjob(args: list[str]) -> None:
    """Build a Kubernetes Job spec from CLI / CallRequest parameters and submit it.

    Loads the cluster configuration (in-cluster first, then local kubeconfig)
    and creates the job via the Batch v1 API.

    Args:
        args: CLI arguments forwarded from the ``startjob`` subcommand.
    """
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config

    _parsed, job, cfg = _build_job_from_args(args, prog="sipstuff-operator startjob")

    try:
        k8s_config.load_incluster_config()
        glogger.info("Loaded in-cluster Kubernetes config")
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
        glogger.info("Loaded local kubeconfig")

    batch_api = k8s_client.BatchV1Api()
    glogger.info("Creating job {} in namespace {}", job.metadata.name, cfg.namespace)
    batch_api.create_namespaced_job(namespace=cfg.namespace, body=job)
    glogger.info("Job {} created successfully", job.metadata.name)


def conntest() -> None:
    """Test Kubernetes API connectivity and exit.

    Attempts to load cluster config, queries the version endpoint, logs
    the result, and exits with code 0 on success or 1 on failure.
    """
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config

    try:
        in_cluster = False
        try:
            k8s_config.load_incluster_config()
            in_cluster = True
            glogger.info("Loaded in-cluster Kubernetes config")
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
            glogger.info("Loaded local kubeconfig")

        configuration = k8s_client.Configuration.get_default_copy()
        glogger.info("API server: {}", configuration.host)

        if not in_cluster:
            _, active_context = k8s_config.list_kube_config_contexts()
            glogger.info("Context: {}", active_context["name"])
            glogger.info("Cluster: {}", active_context["context"].get("cluster", "n/a"))
            glogger.info("User: {}", active_context["context"].get("user", "n/a"))
            namespace = active_context["context"].get("namespace")
            if namespace:
                glogger.info("Namespace: {}", namespace)

        v1 = k8s_client.VersionApi()
        info = v1.get_code()
        glogger.info("Kubernetes API reachable: {} {}", info.git_version, info.platform)
        sys.exit(0)
    except Exception as exc:
        glogger.error("Kubernetes connectivity test failed: {}", exc)
        sys.exit(1)


def main() -> None:
    """Start the sipstuff-k8s-operator HTTP server.

    Prints the startup banner and configuration, creates the FastAPI
    application, and launches uvicorn on the configured port.
    """
    _print_banner()

    try:
        cfg = OperatorConfig.from_env()
        _print_config(cfg)
    except ValueError as exc:
        glogger.error("Configuration error: {}", exc)
        sys.exit(1)

    from sipstuff_k8s_operator.operator import create_app

    app = create_app(cfg)
    uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_level="info", access_log=True, log_config=UVICORN_LOG_CONFIG)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "conntest":
        conntest()
    elif len(sys.argv) > 1 and sys.argv[1] == "dumpjob":
        dumpjob(sys.argv[2:])
    elif len(sys.argv) > 1 and sys.argv[1] == "startjob":
        startjob(sys.argv[2:])
    else:
        main()
