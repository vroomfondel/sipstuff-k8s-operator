import logging
import sys
from dataclasses import fields

import uvicorn
from loguru import logger as glogger
from tabulate import tabulate

from sipstuff_k8s_operator import __version__, configure_logging
from sipstuff_k8s_operator.config import OperatorConfig

configure_logging()
glogger.enable("sipstuff_k8s_operator")

_uvicorn_logger = glogger.bind(classname="uvicorn")


class _LoguruInterceptHandler(logging.Handler):
    """Route stdlib logging records through loguru."""

    def emit(self, record: logging.LogRecord) -> None:
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
    """Recursively remove keys with None values from dicts."""
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(i) for i in obj]
    return obj


def _is_bool_field(annotation: type | None) -> bool:
    """Return True if the Pydantic field annotation resolves to bool."""
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


def dumpjob(args: list[str]) -> None:
    """Build a Job spec from CallRequest parameters and print as YAML or JSON.

    Accepts ``--data`` / ``-d`` for a JSON string and/or individual ``--flags``
    for every CallRequest field.  Flags override JSON values.
    All values are validated through the ``CallRequest`` Pydantic model.

    Usage: ``dumpjob [--json-output] [--data JSON] [--field value ...] [IMAGE]``
    """
    import argparse
    import json as json_mod
    from dataclasses import replace

    from pydantic import ValidationError

    from sipstuff_k8s_operator.job_builder import build_job
    from sipstuff_k8s_operator.models import CallRequest

    parser = argparse.ArgumentParser(prog="sipstuff-operator dumpjob", description="Dump a sample K8s Job spec")
    parser.add_argument("image", nargs="?", default=None, metavar="IMAGE", help="Override job container image")
    parser.add_argument("--json-output", action="store_true", help="Output JSON instead of YAML")
    parser.add_argument("-d", "--data", default=None, help="JSON string matching the CallRequest schema")

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
    merged: dict[str, str | bool] = {}
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

    job = build_job(req, cfg)
    data = _strip_none(job.to_dict())

    if parsed.json_output:
        print(json_mod.dumps(data, indent=2, default=str))
    else:
        import yaml

        print(yaml.dump(data, default_flow_style=False, sort_keys=False))


def conntest() -> None:
    """Test Kubernetes API connectivity and exit."""
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
    else:
        main()
