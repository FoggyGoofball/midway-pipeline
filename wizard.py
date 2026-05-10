#!/usr/bin/env python3
"""
Midway Pipeline — Profile Wizard (CLI Scaffold)
=================================================
A profile-driven architecture CLI for project initialization
and API documentation ingestion.

Commands:
    --init <project_name>   Generate a project manifest in profiles/
    --ingest <url>          Ingest API documentation from a URL (future)
"""

import argparse
import json
import os
import sys
from pathlib import Path


def cmd_init(project_name: str, profiles_dir: Path) -> None:
    """Generate a profiles/<project_name>/manifest.json with placeholder data."""
    project_dir = profiles_dir / project_name
    project_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "project": project_name,
        "version": "0.1.0",
        "status": "scaffolded",
        "domains": [],
        "pipeline_config": {
            "model_7b": "qwen2.5-coder:7b",
            "model_14b": "qwen2.5-coder:14b",
            "max_tasks": 10,
        },
        "created": "2026-05-07",
        "description": f"Auto-generated placeholder manifest for '{project_name}'.",
    }

    manifest_path = project_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"  [Wizard] ✅ Manifest created: {manifest_path}")


def cmd_ingest(url: str, cache_dir: Path) -> None:
    """Placeholder for API documentation ingestion (future)."""
    print(f"  [Wizard] 🔄 Ingest API docs from: {url}")
    print(f"  [Wizard] ⚠️  Ingestion not yet implemented — cache_dir={cache_dir}")


def main() -> None:
    # Determine project root relative to this file
    script_dir = Path(__file__).resolve().parent
    profiles_dir = script_dir / "profiles"
    cache_dir = script_dir / "global_cache"

    parser = argparse.ArgumentParser(
        description="Midway Pipeline Profile Wizard",
    )
    parser.add_argument(
        "--init",
        type=str,
        metavar="PROJECT_NAME",
        help="Initialize a new project profile",
    )
    parser.add_argument(
        "--ingest",
        type=str,
        metavar="URL",
        help="Ingest API documentation from a URL",
    )

    args = parser.parse_args()

    if args.init:
        cmd_init(args.init, profiles_dir)
    elif args.ingest:
        cmd_ingest(args.ingest, cache_dir)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
