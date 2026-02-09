#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
from pprint import pprint


def install_and_import(packagename: str, pipname: str) -> None:
    import importlib

    try:
        importlib.import_module(packagename)
    except ImportError:
        import pip

        pip.main(["install", pipname])
    finally:
        globals()[packagename] = importlib.import_module(packagename)


install_and_import(packagename="github", pipname="pygithub")

from github import Auth, Clones, Github, InputFileContent, RateLimitOverview
from github.Rate import Rate


def _load_include_local() -> None:
    """Load VAR="value" assignments from include.local.sh next to this script."""
    path = Path(__file__).parent / "include.local.sh"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if not _:
            continue
        value = value.strip().strip("\"'")
        os.environ.setdefault(key.strip(), value)


def main() -> None:
    _load_include_local()
    print("update_badge.py::main()")

    # --- KONFIGURATION ---
    repo_token = os.environ["REPO_TOKEN"]
    gist_token = os.environ["GIST_TOKEN"]
    gist_id = os.environ.get("GIST_ID", "69048d146ee747668673b2a8f8ebc81e")
    repo_name = os.environ.get("GITHUB_REPOSITORY", "vroomfondel/sipstuff-k8s-operator")

    history_filename = "sipstuff-k8s-operator_clone_history.json"
    badge_filename = "sipstuff-k8s-operator_clone_count.json"

    # --- 1. VERBINDUNG HERSTELLEN ---
    # Instanz für Gist (Schreibrechte via PAT)
    g_gist = Github(auth=Auth.Token(gist_token))
    # Instanz für Repo (Leserechte via Standard Token reichen meist)
    g_repo = Github(auth=Auth.Token(repo_token))

    # --- 2. DATEN HOLEN ---
    print(f"Hole Daten für Repo: {repo_name}")
    repo = g_repo.get_repo(repo_name)

    # Clones der letzten 14 Tage holen
    clones_data: Clones.Clones | None = repo.get_clones_traffic()

    ndata: int = len(clones_data.clones) if clones_data else 0
    print(f"Datenpunkte erhalten: {ndata}")

    # Alte Historie vom Gist holen
    gist = g_gist.get_gist(gist_id)
    history = {}

    try:
        if history_filename in gist.files:
            content = gist.files[history_filename].content
            history = json.loads(content)
            print("Bestehende Historie geladen.")
        else:
            print("Keine Historie gefunden, starte neu.")
    except Exception as e:
        print(f"Fehler beim Laden der Historie: {e}")

    # --- 3. DATEN MERGEN (Zusammenführen) ---
    # Wir nutzen den Timestamp als Key, um Duplikate zu vermeiden
    if clones_data is not None:
        for c in clones_data.clones:
            # Timestamp zu String konvertieren für JSON Key
            key = str(c.timestamp)
            history[key] = {"count": c.count, "uniques": c.uniques}

    # --- 4. SUMME BERECHNEN ---
    total_clones = sum(d["count"] for d in history.values())
    print(f"Neue Gesamtsumme Clones: {total_clones}")

    # --- 5. JSON FÜR SHIELDS.IO BAUEN ---
    badge_data = {
        "schemaVersion": 1,
        "label": "Clones",
        "message": str(total_clones),
        "color": "blue",
        "namedLogo": "github",
        "logoColor": "white",
    }

    # --- 6. UPDATE DURCHFÜHREN ---
    gist.edit(
        files={
            history_filename: InputFileContent(json.dumps(history, indent=2)),
            badge_filename: InputFileContent(json.dumps(badge_data)),
        }
    )
    print("Gist erfolgreich aktualisiert!")


def get_usage_info() -> None:
    full_api_token: str = os.environ.get("PRIV_FULL_TOKEN", os.environ.get("REPO_TOKEN", ""))

    assert full_api_token is not None and len(full_api_token) > 0

    # Authentifizierung
    g = Github(auth=Auth.Token(full_api_token))

    # Limits abrufen
    limits: RateLimitOverview.RateLimitOverview = g.get_rate_limit()
    print(f"{type(limits)=}")
    print(limits)

    print(f"{type(limits.raw_data)=}")
    pprint(limits.raw_data)

    core = limits.resources.core
    print(f"{type(core)=}")
    print(f"{core=}")

    search = limits.resources.search
    print(f"{type(search)=}")
    print(f"{search=}")

    code_search = limits.resources.code_search
    print(f"{type(code_search)=}")
    print(f"{code_search=}")


if __name__ == "__main__":
    # get_usage_info()
    main()
