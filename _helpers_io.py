"""
_helpers_io.py — File system & hashing utilities for the mesh consensus pipeline.
Contains: audio chime, doc cache, project scanner, file tools,
atomic writes, file hash locking, memory search.

No async/await — purely synchronous.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import subprocess
import textwrap
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from _helpers_exec import PROJECT_ROOT


# ── Audio Chime Utility ─────────────────────────────────────────────────────

def trigger_chime():
    """Play a system beep/chime to alert the user of a gate or important event.

    Uses winsound.MessageBeep on Windows, print('\\a') (ASCII BEL) on other platforms.
    """
    if platform.system() == "Windows":
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            print('\a')
    else:
        print('\a')


# ── LRU Doc Cache ───────────────────────────────────────────────────────────

_DOC_CACHE: Dict[str, Tuple[str, float]] = {}
_DOC_CACHE_TTL = 30.0
_DOC_CACHE_MAX = 20


def _get_doc_cached(rel_path: str, project_root: Path = None) -> str:
    """Read a doc file with LRU caching. Returns content or empty string."""
    from time import time
    now = time()
    pr = project_root or PROJECT_ROOT

    # Check cache
    if rel_path in _DOC_CACHE:
        content, ts = _DOC_CACHE[rel_path]
        if now - ts < _DOC_CACHE_TTL:
            return content

    # Load fresh
    full_path = pr / rel_path
    if not full_path.is_file():
        return ""
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    # Evict oldest if at capacity
    if len(_DOC_CACHE) >= _DOC_CACHE_MAX:
        oldest_key = min(_DOC_CACHE.keys(), key=lambda k: _DOC_CACHE[k][1])
        del _DOC_CACHE[oldest_key]

    _DOC_CACHE[rel_path] = (content, now)
    return content


# ── Project Structure Scanner ─────────────────────────────────────────────

def curate_project_structure(prompt: str, project_root: Path = None) -> str:
    """Scan the project directory tree for files relevant to the prompt."""
    pr = project_root or PROJECT_ROOT
    prompt_lower = prompt.lower()
    relevant_dirs = set()
    dir_keywords = {
        "src": ["engine", "c++", "physics", "render", "shader", "attraction",
                "economy", "modifier", "model", "console", "debug"],
        "attractions": ["plinko", "crumbling", "coin cascade", "coin", "cascade",
                        "claw", "slingshot", "ring toss", "ring", "attraction",
                        "booth", "lua", "script"],
        "assets/shaders": ["shader", "glsl", "render", "bloom", "particle",
                           "vertex", "fragment", "karmic"],
        "assets/audio": ["audio", "sound", "music", "sfx"],
        "assets/textures": ["texture", "sprite", "ui"],
        "assets/meshes": ["mesh", "model", "3d"],
        "assets/fonts": ["font", "text"],
        "docs": ["rules", "docs", "documentation", "bridge", "contract",
                 "spec", "todo", "index"],
        "GDD": ["gdd", "design", "spec", "game design"],
        "resources": ["dialog", "json", "data"],
    }
    for keyword, dirs in dir_keywords.items():
        for d in dirs:
            if d in prompt_lower:
                relevant_dirs.add(keyword)
    relevant_dirs.add("src")
    relevant_dirs.add("docs")
    lines = ["## Project Structure (relevant directories)\n"]
    for d in sorted(relevant_dirs):
        full_path = pr / d
        if not full_path.is_dir():
            continue
        lines.append(f"### {d}/")
        count = 0
        for f in sorted(full_path.iterdir()):
            if f.is_file() and f.suffix in (".cpp", ".h", ".hpp", ".lua", ".py",
                                             ".json", ".md", ".glsl", ".vert",
                                             ".frag", ".txt", ".cfg", ".xml"):
                lines.append(f"  - {f.name}")
                count += 1
                if count >= 10:
                    if len(list(full_path.iterdir())) > 10:
                        lines.append(f"  - ... ({len(list(full_path.iterdir())) - 10} more)")
                    break
        for f in sorted(full_path.iterdir()):
            if f.is_dir():
                lines.append(f"  - {f.name}/")
        lines.append("")
    return "\n".join(lines)


# ── File Tools ─────────────────────────────────────────────────────────────

AGENT_FILE_TOOLS_PROMPT = (
    "\n\n---\n"
    "PROGRESSIVE FILE DISCLOSURE TOOLS:\n"
    "You have access to synchronous file-system tools to read the codebase "
    "during your reasoning. To use them, embed one of these signals:\n"
    "- [FILE_READ:<relative_path>] — Read the contents of a file. "
    "Returns the full text of the file at the given path.\n"
    "  Comma-separated paths are supported, e.g.:\n"
    "  [FILE_READ:src/Engine.cpp, src/Engine.h] — reads multiple files.\n"
    "  Optional line bounds can be specified, e.g.:\n"
    "  [FILE_READ:src/Engine.cpp, lines 300-450] — reads only lines 300-450.\n"
    "  Both can be combined: [FILE_READ:src/Engine.cpp, lines 300-450, src/Engine.h]\n"
    "- [FILE_LIST:<relative_dir>] — List the files in a directory. "
    "Returns a directory listing with file names.\n"
    "  Comma-separated directories are supported, e.g.:\n"
    "  [FILE_LIST:src/, attractions/] — lists multiple directories.\n"
    "The orchestrator will execute the tool and inject the result back into "
    "your context before your next iteration. Use these to explore the "
    "codebase progressively without exceeding your context window.\n"
)


def _read_single_file(pr: Path, path: str) -> str:
    """Read a single file with path traversal check and line-bounds support.

    Supports an optional 'lines N-M' suffix to slice the file content,
    bypassing the 6000-char truncation limit when specific lines are requested.
    """
    path = path.strip()
    # Detect optional line bounds: e.g. "src/Engine.cpp, lines 300-450"
    line_bounds_match = re.search(r',\s*lines\s+(\d+)\s*[-–]\s*(\d+)\s*$', path, re.IGNORECASE)
    start_line = None
    end_line = None
    if line_bounds_match:
        start_line = int(line_bounds_match.group(1))
        end_line = int(line_bounds_match.group(2))
        # Strip the line bounds suffix from the path
        path = path[:line_bounds_match.start()].strip()

    full_path = (pr / path).resolve()
    # Path traversal boundary check
    try:
        full_path.relative_to(pr)
    except ValueError:
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** Path traversal forbidden.\n"
    if not full_path.is_file():
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** File not found or is a directory.\n"
    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
        ext = full_path.suffix.lower()
        lang = {".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
                ".lua": "lua", ".py": "python",
                ".json": "json", ".md": "markdown",
                ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl"}.get(ext, "")
        print(f"  [FileTool] FILE_READ: {path} ({len(content)} chars)")

        # If line bounds are specified, slice the content
        if start_line is not None and end_line is not None:
            lines = content.splitlines()
            # Convert to 0-indexed, clamp to file bounds
            start_idx = max(0, start_line - 1)
            end_idx = min(len(lines), end_line)
            sliced = lines[start_idx:end_idx]
            content = "\n".join(sliced)
            info = f" (lines {start_line}-{end_line}, {len(content)} chars)"
            print(f"  [FileTool]   Sliced{info}")
            # No truncation for targeted line reads
            return f"\n## File Tool Result: {path}{info}\n```{lang}\n{content}\n```\n"

        # Fallback truncation for full-file reads
        if len(content) > 6000:
            content = content[:6000] + "\n\n[... truncated at 6000 chars ...]"
        return f"\n## File Tool Result: {path}\n```{lang}\n{content}\n```\n"
    except Exception as e:
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** {e}\n"


def handle_file_read(signal_content: str, project_root: Path = None) -> str:
    """Synchronous file read tool for agent progressive disclosure.

    Supports comma-separated paths and optional line bounds.
    Example: [FILE_READ:src/Engine.cpp, lines 300-450, src/Engine.h]

    Includes strict path traversal boundary check — any path that resolves
    outside PROJECT_ROOT is rejected with an error.
    """
    pr = (project_root or PROJECT_ROOT).resolve()
    parts = [p.strip() for p in signal_content.split(",") if p.strip()]

    # If only one path (no commas), use the original single-file path
    # But we need to check if it has line bounds first
    if len(parts) == 1:
        return _read_single_file(pr, parts[0])

    # Detect if there's a "lines N-M" specifier — it attaches to the preceding path
    # Strategy: join all parts and check for line pattern, or parse intelligently
    # Simple approach: group consecutive parts that form a path+lines spec
    results = []
    i = 0
    while i < len(parts):
        part = parts[i]
        # Check if this part looks like a line bounds specifier that belongs to previous result
        line_match = re.match(r'^lines\s+(\d+)\s*[-–]\s*(\d+)$', part, re.IGNORECASE)
        if line_match and results:
            # Append line bounds to the last path processed
            last_result_line = results[-1]
            # Re-read with line bounds attached
            results[-1] = None  # placeholder

            # Rebuild the path+lines string for the last item
            # Find what the *actual* path was
            path_with_lines = parts[i-1] + ", lines " + line_match.group(1) + "-" + line_match.group(2)
            results[-1] = _read_single_file(pr, path_with_lines)
            i += 1
            continue
        result = _read_single_file(pr, part)
        if result:
            results.append(result)
        i += 1

    if not results:
        return "\n## File Tool Result: ERROR\n**No valid files or directories specified.**\n"
    return "\n---\n".join(results)


def _list_single_dir(pr: Path, path: str) -> str:
    """List a single directory with path traversal check."""
    path = path.strip()
    full_path = (pr / path).resolve()
    # Path traversal boundary check
    try:
        full_path.relative_to(pr)
    except ValueError:
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** Path traversal forbidden.\n"
    if not full_path.is_dir():
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** Directory not found.\n"
    try:
        files = sorted(full_path.iterdir())
        items = []
        for f in files:
            label = f.name
            if f.is_dir():
                label += "/"
            items.append(label)
        truncated = items[:50]
        if len(items) > 50:
            truncated.append(f"... ({len(items) - 50} more)")
        result = f"\n## File Tool Result: {path}/\n```\n" + "\n".join(truncated) + "\n```\n"
        print(f"  [FileTool] FILE_LIST: {path} ({len(items)} entries)")
        return result
    except Exception as e:
        return f"\n## File Tool Result: ERROR\n**Path:** {path}\n**Error:** {e}\n"


def handle_file_list(signal_content: str, project_root: Path = None) -> str:
    """Synchronous directory listing tool for agent progressive disclosure.

    Supports comma-separated directories.
    Example: [FILE_LIST:src/, attractions/]

    Includes strict path traversal boundary check — any path that resolves
    outside PROJECT_ROOT is rejected with an error.
    """
    pr = (project_root or PROJECT_ROOT).resolve()
    parts = [p.strip() for p in signal_content.split(",") if p.strip()]
    if len(parts) == 1:
        return _list_single_dir(pr, parts[0])
    results = []
    for part in parts:
        result = _list_single_dir(pr, part)
        if result:
            results.append(result)
    if not results:
        return "\n## File Tool Result: ERROR\n**No valid directories specified.**\n"
    return "\n---\n".join(results)


# ── VRAM Stub Builder ─────────────────────────────────────────────────────

def _make_vram_stub(rel_path: str, content: str) -> str:
    """Build a <VRAM_STUB> pointer from file content for the Active Virtual Memory system.

    Extracts the first non-empty line of the file as a summary (capped at 100 chars),
    returning a lightweight pointer that the agent can PAGE_IN on demand.
    """
    first_line = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    if len(first_line) > 100:
        first_line = first_line[:100] + "..."
    return f'<VRAM_STUB id="{rel_path}" summary="{first_line}" />'


# ── Autonomous File Reading ────────────────────────────────────────────────

def find_relevant_files(prompt: str, persona: str, project_root: Path = None) -> list:
    """Scan the project for files relevant to the given prompt and persona."""
    pr = project_root or PROJECT_ROOT
    relevant = []
    prompt_lower = prompt.lower()
    persona_lower = persona.lower()
    keyword_map = {
        "plinko":       ["attractions/plinko/"],
        "crumbling":    ["attractions/crumblingfacade/"],
        "coin cascade": ["attractions/coin_cascade/"],
        "physics":      ["src/PhysicsManager.cpp", "src/PhysicsManager.h",
                         "src/MidwayPhysics.cpp", "src/MidwayPhysics.h"],
        "engine":       ["src/Engine.cpp", "src/Engine.h"],
        "attraction":   ["src/AttractionManager.cpp", "src/AttractionManager.h",
                         "attractions/_shared/"],
        "economy":      ["src/EconomyManager.cpp", "src/EconomyManager.h",
                         "economy_state.json"],
        "modifier":     ["src/ModifierState.h", "modifier_state.json"],
        "lua":          ["attractions/", "init.lua"],
        "shader":       ["assets/shaders/"],
        "render":       ["src/DebugRenderer.cpp", "src/DebugRenderer.h"],
        "model":        ["src/ModelManager.cpp", "src/ModelManager.h"],
        "dev console":  ["src/DevConsole.cpp", "src/DevConsole.h"],
        "dialog":       ["resources/dialog.json"],
        "gdd":          ["GDD/"],
        "jolt":         ["docs/jolt_api.md"],
        "box2d":        ["docs/box2d_api.md"],
        "sol2":         ["docs/sol2_api.md"],
        "opengl":       ["docs/opengl_sdl_api.md"],
        "sdl":          ["docs/opengl_sdl_api.md"],
    }
    rule_map = {
        "c++":    "docs/rules_cpp.md",
        "phys":   "docs/rules_phys.md",
        "shader": "docs/rules_shader.md",
        "lua":    "docs/rules_lua.md",
    }
    # Skip pipeline system directories — not game engine files
    _PIPELINE_EXCLUDED_PREFIXES = {
        "midway-pipeline/",
        "docs/memory/",
        "docs/.pipeline_journal/",
        "docs/rules_",
        ".pipeline_checkpoints/",
        ".pipeline_snapshots/",
        "offload_store/",
        "pipeline_output_",
    }
    candidate_paths = set()
    for keyword, paths in keyword_map.items():
        if keyword in prompt_lower:
            for p in paths:
                # Don't include pipeline-specific paths
                if not any(p.startswith(prefix) for prefix in _PIPELINE_EXCLUDED_PREFIXES):
                    candidate_paths.add(p)
    for key, rule_path in rule_map.items():
        if key in persona_lower:
            candidate_paths.add(rule_path)
    candidate_paths.add("docs/engine_lua_bridge_contract.md")
    candidate_paths.add("docs/rules_review.md")
    candidate_paths.add("docs/rules_logging.md")
    if not candidate_paths:

        candidate_paths = {
            "src/Engine.h", "src/AttractionManager.h",
            "docs/rules_cpp.md", "docs/rules_lua.md",
            "docs/engine_lua_bridge_contract.md",
        }
    for rel_path in sorted(candidate_paths):
        full_path = pr / rel_path
        if full_path.is_file():
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                # VRAM-aware decision: stub or inject raw text
                if (rel_path.startswith("docs/") or rel_path.startswith("GDD/")
                        or len(content) > 1500):
                    stub = _make_vram_stub(rel_path, content)
                    relevant.append((rel_path, stub))
                    print(f"  VRAM stub: {rel_path} ({len(content)} chars -> stub)")
                else:
                    relevant.append((rel_path, content))
                    print(f"  Read: {rel_path} ({len(content)} chars)")
            except Exception as e:
                print(f"  Could not read {rel_path}: {e}")
        elif full_path.is_dir():
            count = 0
            for f in sorted(full_path.rglob("*")):
                if f.is_file() and f.suffix in (".lua", ".cpp", ".h", ".json", ".md", ".glsl", ".vert", ".frag"):
                    try:
                        content = f.read_text(encoding="utf-8", errors="replace")
                        rel = f.relative_to(pr).as_posix()
                        # VRAM-aware: stub docs/ and large files; raw-inject only small src/attractions
                        if (rel.startswith("docs/") or rel.startswith("GDD/")
                                or len(content) > 1500):
                            stub = _make_vram_stub(rel, content)
                            relevant.append((rel, stub))
                            print(f"  VRAM stub: {rel} ({len(content)} chars -> stub)")
                        else:
                            relevant.append((rel, content))
                            print(f"  Read: {rel} ({len(content)} chars)")
                        count += 1
                        if count >= 3:
                            break
                    except Exception:
                        pass
            print(f"  Read {count} files from {rel_path}")
    return relevant


# ── Memory Search ──────────────────────────────────────────────────────────────

def search_memory(query: str = "", project_root: Path = None) -> str:
    """Search the project's docs/memory/ directory for context.

    Globs all *.md files in docs/memory/ and returns their content.
    If a query is provided, filters to files whose name or content matches
    the query string (case-insensitive substring match).

    Args:
        query: Optional search term to filter memory files.
        project_root: Override for PROJECT_ROOT (defaults to global).

    Returns:
        Concatenated content of matching memory files.
    """
    pr = project_root or PROJECT_ROOT
    mem_dir = pr / "docs" / "memory"
    if not mem_dir.is_dir():
        return "(docs/memory/ directory not found)"
    results: List[str] = []
    for mem_file in sorted(mem_dir.glob("*.md")):
        # Skip session timeline to avoid unbounded context
        if mem_file.name == "session_timeline.md":
            continue
        content = mem_file.read_text(encoding="utf-8", errors="replace")
        if not query or query.lower() in mem_file.stem.lower() or query.lower() in content.lower():
            header = f"--- {mem_file.name} ---\n"
            results.append(header + content)

    if not results:
        return f"(no memory files matched query: {query})"

    return "\n\n".join(results)


# ── Atomic File Write Helper ──────────────────────────────────────────────

def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write content to file atomically to prevent corruption from Ctrl+C.

    Writes to a .tmp file first, flushes, then atomically renames to target.
    This prevents half-written files if the process is interrupted.
    """
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    tmp_path.write_text(content, encoding=encoding)
    # Ensure data is flushed to disk by touching the file
    tmp_path.touch()
    # Atomic rename (replace) on all platforms
    tmp_path.replace(path)


# ── File Hash Locking (Pre-Merge Hash Locking) ────────────────────────────

_FILE_HASHES: Dict[str, str] = {}


def compute_file_hash(filepath: str, project_root: Path = None) -> str:
    """Compute MD5 hash of a file, or return '' if file not found."""
    pr = project_root or PROJECT_ROOT
    full_path = (pr / filepath).resolve()
    try:
        full_path.relative_to(pr)
    except ValueError:
        return ""
    if not full_path.is_file():
        return ""
    try:
        content = full_path.read_bytes()
        return hashlib.md5(content).hexdigest()
    except Exception:
        return ""


def save_initial_file_hashes_from_context(ctx_placeholder: Any,
                                          file_context: str,
                                          project_root: Path = None) -> Dict[str, str]:
    """Parse file paths from file_context, compute MD5 hashes, return dict."""
    pr = project_root or PROJECT_ROOT
    hashes: Dict[str, str] = {}
    # Extract file paths from markdown headings like "### File: src/Engine.cpp"
    for match in re.finditer(r'### File:\s*(\S+)', file_context):
        rel_path = match.group(1)
        h = compute_file_hash(rel_path, pr)
        if h:
            hashes[rel_path] = h
    _FILE_HASHES.update(hashes)
    return hashes


def verify_file_hashes(project_root: Path = None) -> List[str]:
    """Re-hash all tracked files. Returns list of changed file paths."""
    pr = project_root or PROJECT_ROOT
    changed: List[str] = []
    for rel_path, old_hash in list(_FILE_HASHES.items()):
        new_hash = compute_file_hash(rel_path, pr)
        if new_hash and new_hash != old_hash:
            changed.append(rel_path)
    return changed


def get_tracked_file_hashes() -> Dict[str, str]:
    """Return the current snapshot of tracked file hashes."""
    return dict(_FILE_HASHES)
