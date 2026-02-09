#!/usr/bin/env python3
"""Check Docker Hub token permissions for all repositories in a namespace."""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request


def list_repositories(namespace: str) -> list[str]:
    """List all repository names for a namespace via Docker Hub API (public repos)."""
    repos: list[str] = []
    url: str | None = f"https://hub.docker.com/v2/repositories/{namespace}/?page_size=100"
    while url:
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"Error listing repositories for '{namespace}': {e}", file=sys.stderr)
            sys.exit(1)
        repos.extend(r["name"] for r in data.get("results", []))
        url = data.get("next")
    return repos


def decode_jwt_payload(token: str) -> dict:
    """Decode the payload of a JWT token."""
    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def basic_auth_header(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def check_permissions(namespace: str, repo: str, username: str, token: str) -> list[str]:
    """Check token permissions for a specific repository via Docker registry auth.

    Probes scopes individually because requesting an unsupported scope (e.g. delete)
    causes the entire auth request to fail with 'insufficient scopes'.
    """
    full_name = f"{namespace}/{repo}"
    granted: list[str] = []

    for scope in ("pull", "push", "delete"):
        url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{full_name}:{scope}"
        req = urllib.request.Request(url, headers={"Authorization": basic_auth_header(username, token)})
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError:
            continue

        decoded = decode_jwt_payload(data["token"])
        for entry in decoded.get("access", []):
            if entry.get("name") == full_name and scope in entry.get("actions", []):
                granted.append(scope)

    return granted


def get_token_info(username: str, token: str) -> dict:
    """Get token metadata from a registry auth response."""
    url = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/alpine:pull"
    req = urllib.request.Request(url, headers={"Authorization": basic_auth_header(username, token)})

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError:
        return {}

    return decode_jwt_payload(data["token"]).get("https://auth.docker.io", {})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check Docker Hub token permissions for all repositories in a namespace."
    )
    parser.add_argument("username", help="Docker Hub username or organization name")
    parser.add_argument("token", help="Docker Hub access token (PAT or OAT)")
    parser.add_argument(
        "-n",
        "--namespace",
        action="append",
        default=[],
        dest="extra_namespaces",
        help="Additional namespace(s) to check (can be specified multiple times)",
    )
    parser.add_argument("--json", dest="json_output", action="store_true", help="Output as JSON instead of ASCII table")
    args = parser.parse_args()

    namespaces = [args.username] + args.extra_namespaces

    token_info = get_token_info(args.username, args.token)

    results = []
    for ns in namespaces:
        repos = list_repositories(ns)
        if not repos:
            print(f"No repositories found for namespace '{ns}'", file=sys.stderr)
            continue
        for repo in sorted(repos):
            actions = check_permissions(ns, repo, args.username, args.token)
            results.append({"repository": f"{ns}/{repo}", "permissions": actions})

    if not results:
        print("No repositories found in any namespace.", file=sys.stderr)
        sys.exit(1)

    if args.json_output:
        print(
            json.dumps(
                {"token_name": token_info.get("at_name"), "plan": token_info.get("plan_name"), "repositories": results},
                indent=2,
            )
        )
        return

    # Token info
    print(f"Token: {token_info.get('at_name', 'N/A')}")
    print(f"Plan:  {token_info.get('plan_name', 'N/A')}")
    print()

    # ASCII table
    repo_w = max(max((len(r["repository"]) for r in results), default=0), len("Repository"))
    perm_w = max(max((len(", ".join(r["permissions"])) for r in results), default=0), len("Permissions"))

    sep = f"+{'-' * (repo_w + 2)}+{'-' * (perm_w + 2)}+"
    print(sep)
    print(f"| {'Repository':<{repo_w}} | {'Permissions':<{perm_w}} |")
    print(sep)
    for r in results:
        perms = ", ".join(r["permissions"]) or "-"
        print(f"| {r['repository']:<{repo_w}} | {perms:<{perm_w}} |")
    print(sep)


if __name__ == "__main__":
    main()
