import sys
from dataclasses import fields

import uvicorn
from loguru import logger as glogger
from tabulate import tabulate

from sipstuff_k8s_operator import __version__, configure_logging
from sipstuff_k8s_operator.config import OperatorConfig

configure_logging()
glogger.enable("sipstuff_k8s_operator")


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


def dumpjob(args: list[str]) -> None:
    """Build a sample Job spec and print it as YAML (default) or JSON (``--json``).

    Usage: ``dumpjob [--json] [IMAGE]``
    """
    from dataclasses import replace

    from sipstuff_k8s_operator.job_builder import build_job
    from sipstuff_k8s_operator.models import CallRequest

    use_json = "--json" in args
    remaining = [a for a in args if a != "--json"]

    cfg = OperatorConfig.from_env()
    if remaining:
        cfg = replace(cfg, job_image=remaining[0])
    req = CallRequest(dest="+4912345", text="Hello world")
    job = build_job(req, cfg)
    data = _strip_none(job.to_dict())

    if use_json:
        import json

        print(json.dumps(data, indent=2, default=str))
    else:
        import yaml

        print(yaml.dump(data, default_flow_style=False, sort_keys=False))


def conntest() -> None:
    """Test Kubernetes API connectivity and exit."""
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config

    try:
        try:
            k8s_config.load_incluster_config()
            glogger.info("Loaded in-cluster Kubernetes config")
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
            glogger.info("Loaded local kubeconfig")

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
    uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_level="info")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "conntest":
        conntest()
    elif len(sys.argv) > 1 and sys.argv[1] == "dumpjob":
        dumpjob(sys.argv[2:])
    else:
        main()
