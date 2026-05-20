#!/usr/bin/env python3
"""
Midway to Nowhere â€” Offline API Documentation Acquisition Script
=================================================================
Downloads, strips, and compresses API documentation for the project's
tech stack into context-token-efficient markdown files with anchors.

Dependencies: Python 3.8+ (stdlib only â€” no pip installs needed)

Usage:
    python fetch_api_docs.py                    # Fetch all docs from web
    python fetch_api_docs.py --from-raw         # Process from docs/_raw/*.html
    python fetch_api_docs.py --list             # List available sources
    python fetch_api_docs.py --source jolt      # Fetch only Jolt docs

Output:
    docs/jolt_api.md
    docs/box2d_api.md
    docs/sol2_api.md
    docs/opengl_sdl_api.md
    docs/cpp17_api.md
    docs/api_index.md

Implementation is split for readability:
    docs/_doc_parsers.py    â€” HTML/C++ parsers and fetch helpers
    docs/_doc_generators.py â€” per-library markdown generators
"""
import sys
from pathlib import Path

DOCS_DIR = Path(__file__).parent.resolve()
RAW_DIR = DOCS_DIR / "_raw"
TOKEN_BUDGET = 8000  # Max tokens per file before splitting

# Ensure the docs/ directory is importable when running as a standalone script
if str(DOCS_DIR) not in sys.path:
    sys.path.insert(0, str(DOCS_DIR))

# â”€â”€ Source configuration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

SOURCES = {
    "jolt": {
        "name": "Jolt Physics",
        "files": [
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Collision/BroadPhase/BroadPhaseLayer.h",
                "local": "jolt_BroadPhaseLayer.h",
                "section": "BroadPhaseLayerInterface",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Body/BodyCreationSettings.h",
                "local": "jolt_BodyCreationSettings.h",
                "section": "BodyCreationSettings",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Constraints/TwoBodyConstraint.h",
                "local": "jolt_TwoBodyConstraint.h",
                "section": "TwoBodyConstraint",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Constraints/Constraint.h",
                "local": "jolt_Constraint.h",
                "section": "Constraint",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Body/Body.h",
                "local": "jolt_Body.h",
                "section": "Body",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Body/BodyID.h",
                "local": "jolt_BodyID.h",
                "section": "BodyID",
            },
            {
                "url": "https://raw.githubusercontent.com/jrouwe/JoltPhysics/master/Jolt/Physics/Collision/ObjectLayer.h",
                "local": "jolt_ObjectLayer.h",
                "section": "ObjectLayer",
            },
        ],
    },
    "box2d": {
        "name": "Box2D",
        "files": [
            {
                "url": "https://raw.githubusercontent.com/erincatto/box2d/main/include/box2d/box2d.h",
                "local": "box2d_box2d.h",
                "section": "box2d",
            },
        ],
    },
    "sol2": {
        "name": "sol2",
        "files": [
            {
                "url": "https://raw.githubusercontent.com/ThePhD/sol2/develop/include/sol/sol.hpp",
                "local": "sol2_sol.hpp",
                "section": "sol",
            },
        ],
    },
    "opengl_sdl": {
        "name": "OpenGL 3.3 + SDL2",
        "files": [
            {
                "url": "https://raw.githubusercontent.com/libsdl-org/SDL/SDL2/include/SDL_video.h",
                "local": "sdl_video.h",
                "section": "SDL_Video",
            },
        ],
    },
    "cpp17": {
        "name": "C++17 Standard Library",
        "files": [
            {
                "url": "https://en.cppreference.com/mwiki/index.php?title=Special%3AExport&page=cpp%2F17",
                "local": "cpp17_standard.html",
                "section": "C++17 Features",
            },
            {
                "url": "https://en.cppreference.com/mwiki/index.php?title=Special%3AExport&page=cpp%2Futility%2Foptional",
                "local": "cpp17_optional.html",
                "section": "std::optional",
            },
            {
                "url": "https://en.cppreference.com/mwiki/index.php?title=Special%3AExport&page=cpp%2Futility%2Fvariant",
                "local": "cpp17_variant.html",
                "section": "std::variant",
            },
            {
                "url": "https://en.cppreference.com/mwiki/index.php?title=Special%3AExport&page=cpp%2Fstring%2Fbasic_string_view",
                "local": "cpp17_string_view.html",
                "section": "std::string_view",
            },
            {
                "url": "https://en.cppreference.com/mwiki/index.php?title=Special%3AExport&page=cpp%2Ffilesystem",
                "local": "cpp17_filesystem.html",
                "section": "std::filesystem",
            },
        ],
    },
}

# â”€â”€ Delegated helpers â€” imported here so callers only need this one file â”€â”€â”€â”€â”€

from _doc_parsers import fetch_url, save_raw, estimate_tokens
from _doc_generators import (
    generate_jolt_doc,
    generate_box2d_doc,
    generate_sol2_doc,
    generate_opengl_sdl_doc,
    generate_cpp17_doc,
    generate_api_index,
)


# â”€â”€ Orchestration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def fetch_all() -> None:
    """Fetch all raw sources from the web."""
    print("=" * 60)
    print("  Fetching API documentation sources...")
    print("=" * 60)

    for source_key, source_info in SOURCES.items():
        print(f"\n--- {source_info['name']} ---")
        for file_info in source_info["files"]:
            content = fetch_url(file_info["url"])
            if content:
                save_raw(content, file_info["local"])

    print("\nDone fetching.")


def generate_all() -> None:
    """Generate all API doc files from raw sources."""
    print("=" * 60)
    print("  Generating API documentation...")
    print("=" * 60)

    generators = [
        ("jolt_api.md",       lambda: generate_jolt_doc(SOURCES)),
        ("box2d_api.md",      generate_box2d_doc),
        ("sol2_api.md",       generate_sol2_doc),
        ("opengl_sdl_api.md", generate_opengl_sdl_doc),
        ("cpp17_api.md",      generate_cpp17_doc),
        ("api_index.md",      generate_api_index),
    ]

    for filename, generator in generators:
        print(f"\n  Generating {filename}...")
        content = generator()
        path = DOCS_DIR / filename
        path.write_text(content, encoding="utf-8")
        tokens = estimate_tokens(content)
        print(f"    Written: {path} (~{tokens} tokens)")

    print("\nDone generating.")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Midway API Documentation Acquisition Script"
    )
    parser.add_argument("--from-raw", action="store_true",
                        help="Process from docs/_raw/*.html instead of fetching")
    parser.add_argument("--list", action="store_true",
                        help="List available sources")
    parser.add_argument("--source", type=str, default=None,
                        help="Fetch only a specific source (jolt, box2d, sol2, opengl_sdl, cpp17)")

    args = parser.parse_args()

    if args.list:
        print("Available sources:")
        for key, info in SOURCES.items():
            print(f"  {key}: {info['name']} ({len(info['files'])} files)")
        return

    if args.from_raw:
        print("Processing from raw files...")
        generate_all()
        return

    if args.source:
        if args.source not in SOURCES:
            print(f"Unknown source: {args.source}")
            print(f"Available: {', '.join(SOURCES.keys())}")
            sys.exit(1)
        print(f"Fetching only: {SOURCES[args.source]['name']}")
        for file_info in SOURCES[args.source]["files"]:
            content = fetch_url(file_info["url"])
            if content:
                save_raw(content, file_info["local"])
        generate_all()
        return

    # Default: fetch all then generate all
    fetch_all()
    generate_all()


if __name__ == "__main__":
    main()

