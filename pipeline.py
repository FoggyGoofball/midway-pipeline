#!/usr/bin/env python3
"""
Midway to Nowhere — Multi-Hop Pipeline Orchestrator
====================================================
Standalone Python script. Calls Ollama directly via HTTP API.
AUTONOMOUSLY reads project files. Features iterative self-correction
loops (up to 10 iterations per task) and checkpoint save/restore.

Usage:
    python pipeline.py "add a jackpot feature to the plinko attraction"
    python pipeline.py --checkpoint <id> "continue from checkpoint"

Output: streams to terminal + saves to pipeline_output_<timestamp>.md
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import shutil
from datetime import datetime
from pathlib import Path

# Import snapshot manager (optional — pipeline works without it)
try:
    from pipeline_snapshot import SnapshotManager
    HAS_SNAPSHOT = True
except ImportError:
    SnapshotManager = None
    HAS_SNAPSHOT = False

# ── Configuration ──────────────────────────────────────────────────────────
OLLAMA_HOST = "http://192.168.0.16:11434"
MODEL = "qwen2.5-coder:7b"
DIRECTOR_MODEL = "qwen2.5-coder:7b"
PROJECT_ROOT = Path(__file__).parent.resolve()
MAX_ITERATIONS = 10
OLLAMA_TIMEOUT = 420
CHECKPOINT_DIR = PROJECT_ROOT / ".pipeline_checkpoints"

# ── Domain Availability (gated by project state) ───────────────────────────
# These are dynamically determined by get_project_state(), but the master list
# of ALL possible agents (including ones not yet ready) is kept here.
ALL_DOMAINS = {
    "C++": {
        "tag": "[C++]",
        "ready": True,
        "description": "Engine architecture, physics integration, rendering, memory, Vicious Cycle seam, modifier system, object pools, booth lifecycle, AND all networking (the C++ engine handles all network interactions)",
        "system_prompt": (
            "You are the C++17 systems engineer for 'Midway to Nowhere'. "
            "Write ONLY C++17. Use SDL2, OpenGL 3.3+, nlohmann/json. "
            "Be aware of the 'Vicious Cycle' spatial seam (teleporting bodies to Z=0)."
        ),
        "name": "C++ Core",
    },
    "PHYS": {
        "tag": "[PHYS]",
        "ready": True,
        "description": "Jolt/Box2D physics, teleport stability, kinematic control, collision layers, sensors",
        "system_prompt": (
            "You are the Lead Physics Architect for 'Midway to Nowhere'. "
            "Focus on Jolt/Box2D stability and state-corruption during 'Vicious Cycle' teleports. "
            "Analyze MSVC diagnostics and call stacks for memory leaks."
        ),
        "name": "Physics Architect",
    },
    "SHADER": {
        "tag": "[SHADER]",
        "ready": False,
        "description": "GLSL shaders, Karmic-Temporal Matrix, PS1 vertex snapping, bloom, particles",
        "system_prompt": (
            "You are the Rendering Expert for 'Midway to Nowhere'. "
            "Specialize in OpenGL 3.3+ pipelines, GLSL shader optimization, "
            "and managing the 'Midway Host' vertex buffer objects (VBOs). "
            "Ensure all rendering code integrates cleanly with the SDL2 windowing context."
        ),
        "name": "Shader Expert",
    },
    "Lua": {
        "tag": "[Lua]",
        "ready": True,
        "description": "Attraction scripts, UI, economy, modifier consumption, OnLoad/OnStep/OnUnload",
        "system_prompt": (
            "You are the gameplay scripter for 'Midway to Nowhere'. "
            "Focus on Lua 5.4 and sol2 bindings to the C++ host."
        ),
        "name": "Lua Scripter",
    },
}

# Build PERSONA_MAP from ready domains only
PERSONA_MAP = {}
for key, domain in ALL_DOMAINS.items():
    if domain["ready"]:
        PERSONA_MAP[key] = {"system": domain["system_prompt"], "name": domain["name"]}
    # Also add lowercase variant
    if domain["ready"]:
        PERSONA_MAP[key.lower()] = {"system": domain["system_prompt"], "name": domain["name"]}


# ── System Prompts ─────────────────────────────────────────────────────────

DIRECTOR_SYSTEM = (
    "You are the PROJECT DIRECTOR for 'Midway to Nowhere' game project. "
    "Your ONLY job: decompose feature requests into 1-5 tasks, each tagged with an available domain. "
    "Output ONLY the task list. NO code. NO explanations. NO commentary."
)

REVIEW_SYSTEM = (
    "You are the INTEGRATION REVIEWER for 'Midway to Nowhere'. "
    "Your ONLY job: review generated code against engine rules and identify issues. "
    "Do NOT write code. Do NOT fix problems. "
    "End your review with **PASS** or **FAIL** on its own line."
)

REVIEW_PROMPT = (
    "Review the generated code below. Check for:\n"
    "1. Cross-domain issues: C++ bridge <-> Lua calls\n"
    "2. Rule compliance: Check against docs/rules_cpp.md, rules_lua.md, rules_phys.md, rules_shader.md\n"
    "3. Vicious Cycle consistency: C++ applies teleport, Lua does not decide\n"
    "4. Modifier system: All 9 values synced across all layers\n"
    "5. Error handling: No raw pointers, server-authoritative economy\n\n"
    "OUTPUT FORMAT:\n"
    "## Integration Review\n"
    "### Issues\n"
    "- Issue 1: ...\n"
    "### Verdict\n"
    "**PASS** or **FAIL**\n\n"
    "End with **PASS** or **FAIL** on its own line."
)

FINAL_APPROVAL_SYSTEM = (
    "You are the PROJECT DIRECTOR for 'Midway to Nowhere'. "
    "Review the completed work and either APPROVE or request REVISIONS. "
    "Start your response with **APPROVED** or **REVISION REQUIRED**."
)

SELF_CORRECT_SYSTEM = (
    "You are a code reviewer examining your own previous output. "
    "Identify errors, bugs, or missing pieces, then produce an improved version. "
    "If no issues found, state 'NO ISSUES FOUND' and repeat your previous output unchanged."
)

ARCHITECT_FIX_SYSTEM = (
    "You are the domain architect for 'Midway to Nowhere'. "
    "The Integration Reviewer has identified issues in your code. "
    "Fix ALL reported issues and produce corrected code. "
    "Address every issue the Reviewer raised. "
    "If you believe an issue is a false positive, explain why."
)

LIBRARIAN_SYSTEM = (
    "You are the GDD LIBRARIAN for 'Midway to Nowhere'. "
    "Your ONLY job: given a feature request, identify which sections of the "
    "Game Design Document (GDD) are relevant. Output ONLY the section names "
    "and a 1-sentence summary of why each is relevant. NO code. NO commentary."
)


# ── GDD Librarian ──────────────────────────────────────────────────────────

GDD_PATH = PROJECT_ROOT / "GDD" / "Midway_to_Nowhere_Master_GDD_v19.md"

GDD_SECTION_MAP = {
    "executive":       {"start": 24, "end": 33, "label": "1. Executive Summary & Core Architecture"},
    "technical":       {"start": 34, "end": 52, "label": "2. Technical Backbone: Custom C++ Engine"},
    "economy":         {"start": 53, "end": 57, "label": "3. Dual-Currency Economy & Streak Protocol"},
    "modifier":        {"start": 58, "end": 73, "label": "4. Global Identity Stats & Modifiers"},
    "loot":            {"start": 74, "end": 78, "label": "5. Augmentation & Bribery Loop"},
    "pacing":          {"start": 79, "end": 103, "label": "6. Pacing, Onboarding, Meta-Progression"},
    "concessions":     {"start": 104, "end": 108, "label": "7. Concessions & Utility Stalls"},
    "coin cascade":    {"start": 113, "end": 118, "label": "8. The Coin Cascade"},
    "plinko":          {"start": 119, "end": 124, "label": "8. Purgatorial Plinko"},
    "claw":            {"start": 125, "end": 130, "label": "8. The Claw of Condemnation"},
    "slingshot":       {"start": 131, "end": 136, "label": "8. The Slingshot Array"},
    "ring toss":       {"start": 137, "end": 142, "label": "8. The Ring Toss"},
    "crumbling":       {"start": 143, "end": 150, "label": "8. The Crumbling Façade"},
    "future":          {"start": 151, "end": 160, "label": "9. Midway Expansion Catalog"},
    "boss":            {"start": 161, "end": 244, "label": "10. Identity Loadouts & Boss Encounters"},
    "endgame":         {"start": 245, "end": 248, "label": "11. Terminal Wagers & True Endgame"},
    "narrative":       {"start": 249, "end": 331, "label": "12-13. Narrative & Origins"},
    "roadmap":         {"start": 332, "end": 336, "label": "14. Solo Developer Roadmap"},
}

KEYWORD_TO_SECTION = {
    "plinko": "plinko", "coin cascade": "coin cascade", "coin": "coin cascade",
    "cascade": "coin cascade", "crumbling": "crumbling", "facade": "crumbling",
    "claw": "claw", "slingshot": "slingshot", "ring toss": "ring toss",
    "ring": "ring toss", "economy": "economy", "token": "economy",
    "ticket": "economy", "currency": "economy", "modifier": "modifier",
    "stats": "modifier", "karma": "modifier", "boss": "boss", "bosses": "boss",
    "encounter": "boss", "narrative": "narrative", "story": "narrative",
    "lore": "narrative", "engine": "technical", "c++": "technical",
    "physics": "technical", "rendering": "technical", "shader": "technical",
    "lua": "technical", "pacing": "pacing", "onboarding": "pacing",
    "meta": "pacing", "concession": "concessions", "food": "concessions",
    "loot": "loot", "prize": "loot", "augment": "loot", "future": "future",
    "expansion": "future", "endgame": "endgame", "terminal": "endgame",
    "roadmap": "roadmap", "executive": "executive", "summary": "executive",
}


def get_project_state() -> str:
    """
    Reads docs/completed_features.md and docs/todo.md to produce a
    concise summary of what's built vs what's not started.
    Dynamically determines which domains are available for task assignment.
    """
    lines = ["## Current Project State\n"]

    # Read completed features
    completed_path = PROJECT_ROOT / "docs" / "completed_features.md"
    if completed_path.is_file():
        try:
            text = completed_path.read_text(encoding="utf-8", errors="replace")
            done_sections = re.findall(r"### ✅ (.+)", text)
            if done_sections:
                lines.append("### ✅ Implemented Systems")
                for s in done_sections[:15]:
                    lines.append(f"- {s}")
                if len(done_sections) > 15:
                    lines.append(f"- ... ({len(done_sections) - 15} more)")
                lines.append("")
        except Exception:
            pass

    # Read todo for what's not started
    todo_path = PROJECT_ROOT / "docs" / "todo.md"
    if todo_path.is_file():
        try:
            text = todo_path.read_text(encoding="utf-8", errors="replace")
            not_started = re.findall(r"\|\s*\d+\s*\|.*?\| ⬜ (?:Open|Next)\s*\|(.+?)\|", text)
            if not_started:
                lines.append("### ⬜ Not Yet Implemented")
                for s in not_started[:10]:
                    lines.append(f"- {s.strip()}")
                if len(not_started) > 10:
                    lines.append(f"- ... ({len(not_started) - 10} more)")
                lines.append("")
        except Exception:
            pass

    # Dynamically report domain availability
    lines.append("### 🟢 Available Domains (ready for task assignment)")
    for key, domain in ALL_DOMAINS.items():
        if domain["ready"]:
            lines.append(f"- {domain['tag']} {domain['description']}")
    lines.append("")

    lines.append("### 🔴 Unavailable Domains (not ready — do NOT assign)")
    for key, domain in ALL_DOMAINS.items():
        if not domain["ready"]:
            lines.append(f"- {domain['tag']} {domain['description']}")
    lines.append("")

    # Explicitly state what does NOT exist
    lines.append("### ❌ Does NOT Exist (do not assign tasks for these)")
    lines.append("- No networking/multiplayer code at all")
    lines.append("- No Box2D physics integration")
    lines.append("- No audio engine (SoLoud not integrated)")
    lines.append("- No save/load system")
    lines.append("- No boss encounters")
    lines.append("- No prize/augment runtime loading")
    lines.append("- No Barker billboarding system")
    lines.append("")

    return "\n".join(lines)


def get_available_domains_text() -> str:
    """Return a concise text listing only available domains for the Director prompt."""
    parts = []
    for key, domain in ALL_DOMAINS.items():
        if domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


def get_unavailable_domains_text() -> str:
    """Return a concise text listing unavailable domains."""
    parts = []
    for key, domain in ALL_DOMAINS.items():
        if not domain["ready"]:
            parts.append(f"- {domain['tag']} {domain['description']}")
    return "\n".join(parts)


def build_director_prompt() -> str:
    """Build the Director prompt dynamically based on available domains."""
    available = get_available_domains_text()
    unavailable = get_unavailable_domains_text()

    return (
        "Decompose this feature request into 1-5 tasks. "
        "Each task must have a domain tag and a short title.\n\n"
        "IMPORTANT: You may assign as FEW as 1 task or as MANY as 5. "
        "Only create tasks that are absolutely necessary.\n\n"
        "AVAILABLE DOMAINS (use ONLY these):\n"
        f"{available}\n\n"
        "UNAVAILABLE DOMAINS (do NOT use these — the project has no code for them):\n"
        f"{unavailable}\n\n"
        "RULES:\n"
        "- Do NOT use [NET] — there is no networking code in the project.\n"
        "- Do NOT use [SHADER] — shader effects are not yet implemented.\n"
        "- Do NOT assign [Lua] tasks that write network code.\n"
        "- Only assign a domain if the project actually has code for it.\n\n"
        "Order by dependency: [C++] first, then [PHYS], then [Lua].\n\n"
        "OUTPUT FORMAT (exactly):\n"
        "## Task Breakdown: [Feature Name]\n"
        "### Task 1: [DOMAIN] - [Short Title]\n"
        "### Task 2: [DOMAIN] - [Short Title]\n"
        "...\n\n"
        "CRITICAL: Do NOT write any code. Only list tasks."
    )


def curate_project_structure(prompt: str) -> str:
    """Scan the project directory tree for files relevant to the prompt."""
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
        full_path = PROJECT_ROOT / d
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


def extract_gdd_sections(prompt: str) -> str:
    """GDD Librarian: extract only relevant GDD sections based on the prompt."""
    if not GDD_PATH.is_file():
        return ""

    prompt_lower = prompt.lower()

    selected_keys = set()
    for keyword, section_key in KEYWORD_TO_SECTION.items():
        if keyword in prompt_lower:
            selected_keys.add(section_key)

    selected_keys.add("executive")
    selected_keys.add("technical")

    try:
        lines = GDD_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""

    excerpts = []
    for key in sorted(selected_keys, key=lambda k: GDD_SECTION_MAP[k]["start"]):
        section = GDD_SECTION_MAP.get(key)
        if not section:
            continue
        start = section["start"] - 1
        end = min(section["end"], len(lines))
        if start >= end:
            continue
        section_lines = lines[start:end]
        excerpts.append(f"### {section['label']}\n" + "\n".join(section_lines))

    if not excerpts:
        return ""

    result = "## Relevant GDD Excerpts\n" + "\n\n".join(excerpts)
    print(f"  [GDD Librarian] Extracted {len(selected_keys)} section(s) from GDD")
    return result


def recursive_librarian(prompt: str, depth: int = 0, max_depth: int = 3) -> str:
    """
    Recursively call the GDD Librarian to refine context.
    If the initial extraction is vague, the Librarian can request more specific
    sections, and we re-query with refined keywords.
    """
    if depth >= max_depth:
        return ""

    result = extract_gdd_sections(prompt)

    # If we got something useful, return it
    if result and len(result) > 100:
        return result

    # Otherwise, ask the Librarian model what sections to look for
    librarian_prompt = (
        f"Given this feature request: '{prompt}'\n\n"
        f"Which GDD sections would be most relevant? "
        f"Available sections: {', '.join(GDD_SECTION_MAP.keys())}\n"
        f"Output only the section names, comma-separated."
    )

    librarian_response = call_ollama(
        LIBRARIAN_SYSTEM,
        librarian_prompt,
        f"GDD Librarian (recursive depth {depth + 1})",
    )

    # Parse the response for section names
    found_sections = []
    for key in GDD_SECTION_MAP.keys():
        if key.lower() in librarian_response.lower():
            found_sections.append(key)

    if found_sections:
        # Re-extract with the refined sections
        refined_prompt = prompt + " " + " ".join(found_sections)
        return recursive_librarian(refined_prompt, depth + 1, max_depth)

    return result


# ── Autonomous File Reading ────────────────────────────────────────────────

def find_relevant_files(prompt: str, persona: str) -> list:
    """Scan the project for files relevant to the given prompt and persona."""
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
    }

    rule_map = {
        "c++":    "docs/rules_cpp.md",
        "phys":   "docs/rules_phys.md",
        "shader": "docs/rules_shader.md",
        "lua":    "docs/rules_lua.md",
    }

    candidate_paths = set()
    for keyword, paths in keyword_map.items():
        if keyword in prompt_lower:
            for p in paths:
                candidate_paths.add(p)
    for key, rule_path in rule_map.items():
        if key in persona_lower:
            candidate_paths.add(rule_path)
    candidate_paths.add("docs/engine_lua_bridge_contract.md")
    candidate_paths.add("docs/rules_review.md")
    if not candidate_paths:
        candidate_paths = {
            "src/Engine.h", "src/AttractionManager.h",
            "docs/rules_cpp.md", "docs/rules_lua.md",
            "docs/engine_lua_bridge_contract.md",
        }

    for rel_path in sorted(candidate_paths):
        full_path = PROJECT_ROOT / rel_path
        if full_path.is_file():
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                if len(content) > 6000:
                    content = content[:6000] + "\n\n[... truncated ...]"
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
                        if len(content) > 4000:
                            content = content[:4000] + "\n\n[... truncated ...]"
                        rel = f.relative_to(PROJECT_ROOT).as_posix()
                        relevant.append((rel, content))
                        count += 1
                        if count >= 3:
                            break
                    except Exception:
                        pass
            print(f"  Read {count} files from {rel_path}")

    return relevant


def format_file_context(files: list) -> str:
    """Format discovered files into a context block for the model."""
    if not files:
        return ""
    parts = ["## Relevant Project Files\n"]
    for path, content in files:
        ext = Path(path).suffix.lower()
        lang = {".cpp": "cpp", ".h": "cpp", ".hpp": "cpp",
                ".lua": "lua", ".py": "python",
                ".json": "json", ".md": "markdown",
                ".glsl": "glsl", ".vert": "glsl", ".frag": "glsl"}.get(ext, "")
        parts.append(f"### File: {path}")
        parts.append("```" + lang)
        parts.append(content)
        parts.append("```\n")
    return "\n".join(parts)


# ── Ollama API Call ────────────────────────────────────────────────────────

def call_ollama(system: str, user: str, label: str, model: str = None) -> str:
    """Call Ollama's /api/chat endpoint. Returns the full response text."""
    use_model = model or MODEL
    print(f"\n{'='*60}")
    print(f"  [{label}] Calling Ollama ({use_model})...")
    print(f"{'='*60}")
    sys.stdout.flush()

    payload = {
        "model": use_model,
        "stream": False,
        "options": {
            "num_ctx": 8192,
        },
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("message", {}).get("content", "")
            print(content)
            sys.stdout.flush()
            return content
    except urllib.error.URLError as e:
        msg = f"[ERROR] Could not reach Ollama at {OLLAMA_HOST}: {e.reason}"
        print(msg)
        return msg
    except json.JSONDecodeError as e:
        msg = f"[ERROR] Invalid JSON response from Ollama: {e}"
        print(msg)
        return msg
    except Exception as e:
        msg = f"[ERROR] {e}"
        print(msg)
        return msg


# ── Task Parsing ───────────────────────────────────────────────────────────

def parse_tasks(director_output: str):
    """Parse tasks from Director output. Returns list of {id, persona, title}."""
    tasks = []
    patterns = [
        r"### Task (\d+): \[([^\]]+)\] — (.+)",
        r"### Task (\d+): \[([^\]]+)\] - (.+)",
        r"### Task (\d+):\s*\[([^\]]+)\]\s*[-–—]\s*(.+)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, director_output):
            tasks.append({
                "id": int(match.group(1)),
                "persona": match.group(2).strip(),
                "title": match.group(3).strip(),
            })
        if tasks:
            break

    # Fallback: if no tasks parsed, create sensible defaults
    if not tasks:
        prompt_lower = director_output.lower()
        if "lua" in prompt_lower or "script" in prompt_lower:
            tasks.append({"id": 1, "persona": "Lua", "title": "Lua Implementation"})
        elif "phys" in prompt_lower:
            tasks.append({"id": 1, "persona": "PHYS", "title": "Physics Implementation"})
        else:
            tasks.append({"id": 1, "persona": "C++", "title": "Full Implementation"})

    return tasks


def has_passed(review_text: str) -> bool:
    """Check if the review output contains a PASS verdict."""
    if re.search(r"\*\*PASS\*\*", review_text):
        return True
    if re.search(r"(?m)^PASS$", review_text):
        return True
    return False


# ── Checkpoint System ──────────────────────────────────────────────────────

def save_checkpoint(checkpoint_id: str, phase: str, data: dict) -> str:
    """
    Save a pipeline checkpoint. Returns the checkpoint file path.
    Checkpoints are stored in .pipeline_checkpoints/<id>/
    """
    checkpoint_dir = CHECKPOINT_DIR / checkpoint_id
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    checkpoint = {
        "checkpoint_id": checkpoint_id,
        "phase": phase,
        "timestamp": datetime.now().isoformat(),
        "data": data,
    }

    path = checkpoint_dir / f"{phase}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, indent=2, ensure_ascii=False)

    print(f"  [Checkpoint] Saved: {phase} ({path})")
    return str(path)


def load_checkpoint(checkpoint_id: str, phase: str = None) -> dict:
    """Load a pipeline checkpoint. If phase is None, load the latest."""
    checkpoint_dir = CHECKPOINT_DIR / checkpoint_id
    if not checkpoint_dir.exists():
        return {}

    if phase:
        path = checkpoint_dir / f"{phase}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    # Load latest phase
    phases_order = ["librarian", "director", "tasks", "review", "final_approval"]
    for p in reversed(phases_order):
        path = checkpoint_dir / f"{p}.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    return {}


def list_checkpoints() -> list:
    """List all saved checkpoints."""
    if not CHECKPOINT_DIR.exists():
        return []

    checkpoints = []
    for d in sorted(CHECKPOINT_DIR.iterdir()):
        if not d.is_dir():
            continue
        # Find the latest phase file
        phases_order = ["librarian", "director", "tasks", "review", "final_approval"]
        latest = None
        for p in reversed(phases_order):
            path = d / f"{p}.json"
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        latest = json.load(f)
                except Exception:
                    pass
                break
        if latest:
            checkpoints.append(latest)
    return checkpoints


# ── Iterative Task Execution ───────────────────────────────────────────────

def execute_task_with_iterations(
    persona_system: str,
    persona_name: str,
    task_id: int,
    task_title: str,
    user_prompt: str,
    director_output: str,
    file_context: str,
    prev_results: list,
) -> str:
    """Execute a single task with up to MAX_ITERATIONS self-correction loops."""
    context_parts = [
        f"## Original Feature Request\n{user_prompt}",
        f"## Director's Task Breakdown\n{director_output}",
        f"## Current Task: {task_title}",
    ]
    if file_context:
        context_parts.append(file_context)
    if prev_results:
        context_parts.append("## Previous Task Results\n" + "\n\n".join(prev_results))

    initial_prompt = "\n\n".join(context_parts)

    print(f"\n  [{persona_name}] Attempt 1/{MAX_ITERATIONS}...")
    output = call_ollama(persona_system, initial_prompt, f"{persona_name} (Task {task_id})")

    for iteration in range(2, MAX_ITERATIONS + 1):
        print(f"\n  [{persona_name}] Self-correction iteration {iteration}/{MAX_ITERATIONS}...")

        correction_prompt = (
            f"## Your Previous Output (Task: {task_title})\n\n"
            f"{output}\n\n"
            f"---\n"
            f"Review the code above. Identify any bugs, errors, missing edge cases, "
            f"or violations of the project's rules. Then produce an improved version.\n\n"
            f"If the code is already correct and complete, respond with 'NO ISSUES FOUND' "
            f"followed by your code unchanged."
        )

        new_output = call_ollama(
            SELF_CORRECT_SYSTEM,
            correction_prompt,
            f"{persona_name} (Task {task_id}, iteration {iteration})",
        )

        if "NO ISSUES FOUND" in new_output.upper():
            print(f"\n  [{persona_name}] Self-correction complete (no issues found)")
            idx = new_output.upper().find("NO ISSUES FOUND")
            remainder = new_output[idx + len("NO ISSUES FOUND"):].strip()
            if remainder:
                output = remainder
            break

        output = new_output

    return output


# ── Review Loop ────────────────────────────────────────────────────────────

def review_with_iterations(
    user_prompt: str,
    director_output: str,
    all_code: str,
    review_context: str,
) -> tuple:
    """
    Run the Reviewer. If it FAILs, send issues back to the architect to fix,
    then re-review. Loop until PASS or MAX_ITERATIONS exhausted.
    Returns (review_output, final_code, passed).
    """
    current_code = all_code
    review_history = []

    for cycle in range(1, MAX_ITERATIONS + 1):
        print(f"\n  [Reviewer] Review cycle {cycle}/{MAX_ITERATIONS}...")

        review_input = (
            f"## Original Feature Request\n{user_prompt}\n\n"
            f"## Director's Task Breakdown\n{director_output}\n\n"
            f"## All Generated Code\n{current_code}\n\n"
            f"{review_context}\n\n"
            f"{REVIEW_PROMPT}\n\n"
            f"Perform cross-domain validation and rule compliance check. "
            f"Do NOT write any new code. Only identify issues."
        )

        review_output = call_ollama(
            REVIEW_SYSTEM,
            review_input,
            f"Integration Reviewer (cycle {cycle})",
        )
        review_history.append(review_output)

        if has_passed(review_output):
            print(f"\n  [Reviewer] PASSED on cycle {cycle}")
            return review_output, current_code, True

        print(f"\n  [Reviewer] FAILED on cycle {cycle}. Sending issues back to architect...")

        fix_prompt = (
            f"## Original Feature Request\n{user_prompt}\n\n"
            f"## Director's Task Breakdown\n{director_output}\n\n"
            f"## Your Current Code\n{current_code}\n\n"
            f"## Integration Reviewer's Issues (Cycle {cycle})\n{review_output}\n\n"
            f"---\n"
            f"Fix ALL issues listed above. Address each one explicitly. "
            f"Output the complete corrected code."
        )

        fixed_code = call_ollama(
            ARCHITECT_FIX_SYSTEM,
            fix_prompt,
            f"Architect Fix (cycle {cycle})",
        )
        current_code = fixed_code

    print(f"\n  [Reviewer] Max cycles ({MAX_ITERATIONS}) reached without PASS")
    return review_history[-1], current_code, False


# ── Main Pipeline ──────────────────────────────────────────────────────────

def run_pipeline(user_prompt: str, checkpoint_id: str = None) -> str:
    """Run the full pipeline with iterative self-correction loops and checkpoints."""
    output_parts = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"# Midway Pipeline Orchestrator\n**Request:** {user_prompt}\n**Date:** {timestamp}\n\n---\n"
    print(header)
    output_parts.append(header)

    # Generate or use checkpoint ID
    ckpt_id = checkpoint_id or datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Phase 0: GDD Librarian ───────────────────────────────────────────
    print("\n## Phase 0/4: GDD Librarian — Curating Context\n")
    output_parts.append("## Phase 0/4: GDD Librarian — Curating Context\n")

    # Try to load from checkpoint first
    ckpt = load_checkpoint(ckpt_id, "librarian")
    if ckpt:
        print(f"  [Checkpoint] Restored from librarian checkpoint")
        gdd_excerpts = ckpt.get("data", {}).get("gdd_excerpts", "")
        project_tree = ckpt.get("data", {}).get("project_tree", "")
        project_state = ckpt.get("data", {}).get("project_state", "")
    else:
        # Recursive Librarian: try keyword-based first, then ask the model
        gdd_excerpts = recursive_librarian(user_prompt)
        if gdd_excerpts:
            print(f"  [GDD Librarian] Extracted {gdd_excerpts.count('###')} GDD section(s)")
            output_parts.append(gdd_excerpts)
        else:
            print("  [GDD Librarian] No GDD found or no relevant sections identified")
            output_parts.append("_No GDD excerpts available._\n")

        project_tree = curate_project_structure(user_prompt)
        if project_tree:
            print(f"  [GDD Librarian] Curated project structure listing")
            output_parts.append(project_tree)

        project_state = get_project_state()
        if project_state:
            print(f"  [GDD Librarian] Loaded project state summary")
            output_parts.append(project_state)

        # Save checkpoint
        save_checkpoint(ckpt_id, "librarian", {
            "gdd_excerpts": gdd_excerpts,
            "project_tree": project_tree,
            "project_state": project_state,
        })

    # Initialize snapshot manager for this run
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot = None
    if HAS_SNAPSHOT:
        try:
            snapshot = SnapshotManager(
                run_id=run_id,
                description=user_prompt[:80],
            )
            print(f"  [Snapshot] Initialized run: {run_id}")
            output_parts.append(f"\n### Snapshots\nRun ID: `{run_id}`\n")
            output_parts.append(
                "Use `python pipeline_snapshot.py list` to view all runs.\n"
                f"Use `python pipeline_snapshot.py diff {run_id}` to see changes.\n"
                f"Use `python pipeline_snapshot.py revert {run_id}` to undo.\n"
                f"Use `python pipeline_snapshot.py advance {run_id}` to apply.\n"
            )
        except Exception as e:
            print(f"  [Snapshot] Warning: could not initialize: {e}")
            snapshot = None

    # ── Phase 1: Director ────────────────────────────────────────────────
    print("\n## Phase 1/4: Director — Task Decomposition\n")
    output_parts.append("## Phase 1/4: Director — Task Decomposition\n")

    ckpt = load_checkpoint(ckpt_id, "director")
    if ckpt:
        print(f"  [Checkpoint] Restored from director checkpoint")
        director_output = ckpt.get("data", {}).get("director_output", "")
    else:
        director_prompt = build_director_prompt()
        director_input = f"{director_prompt}\n\n"
        if gdd_excerpts:
            director_input += f"{gdd_excerpts}\n\n"
        if project_tree:
            director_input += f"{project_tree}\n\n"
        if project_state:
            director_input += f"{project_state}\n\n"
        director_input += f"---\nFEATURE REQUEST:\n{user_prompt}"

        director_output = call_ollama(
            DIRECTOR_SYSTEM,
            director_input,
            "Director",
            model=DIRECTOR_MODEL,
        )
        output_parts.append(director_output)

        # Save checkpoint
        save_checkpoint(ckpt_id, "director", {
            "director_output": director_output,
        })

    # ── Parse tasks ──────────────────────────────────────────────────────
    tasks = parse_tasks(director_output)
    print(f"\n  -> Parsed {len(tasks)} task(s) from Director output")
    for t in tasks:
        print(f"     Task {t['id']}: [{t['persona']}] {t['title']}")

    # ── Phase 2: Execute Tasks (with iterative self-correction) ──────────
    print(f"\n## Phase 2/4: Executing {len(tasks)} Task(s) (up to {MAX_ITERATIONS} iterations each)\n")
    output_parts.append(f"\n## Phase 2/4: Executing {len(tasks)} Task(s)\n")

    task_results = {}

    for task in tasks:
        persona = PERSONA_MAP.get(task["persona"], PERSONA_MAP.get("C++", {"system": "", "name": "Unknown"}))
        print(f"\n### Task {task['id']}: [{task['persona']}] {task['title']}\n")
        output_parts.append(f"\n### Task {task['id']}: [{task['persona']}] {task['title']}\n")

        # Check for task checkpoint
        ckpt = load_checkpoint(ckpt_id, f"task_{task['id']}")
        if ckpt:
            print(f"  [Checkpoint] Restored from task {task['id']} checkpoint")
            output = ckpt.get("data", {}).get("output", "")
        else:
            # Autonomously read files relevant to this task
            print(f"  [{persona['name']}] Scanning project for relevant files...")
            task_files = find_relevant_files(
                f"{user_prompt} {task['title']}",
                task["persona"],
            )
            file_context = format_file_context(task_files)

            prev_results = [
                f"### Result from Task {tid}\n{result}"
                for tid, result in task_results.items()
                if tid < task["id"]
            ]

            output = execute_task_with_iterations(
                persona_system=persona["system"],
                persona_name=persona["name"],
                task_id=task["id"],
                task_title=task["title"],
                user_prompt=user_prompt,
                director_output=director_output,
                file_context=file_context,
                prev_results=prev_results,
            )

            # Save checkpoint
            save_checkpoint(ckpt_id, f"task_{task['id']}", {
                "persona": task["persona"],
                "title": task["title"],
                "output": output,
            })

        task_results[task["id"]] = output
        output_parts.append(output)

        # Snapshot: save agent output and originals of referenced files
        if snapshot:
            try:
                if file_context:
                    snapshot.save_originals_from_context(file_context)
                snapshot.save_agent_output(persona["name"], task["id"], output)
            except Exception as e:
                print(f"  [Snapshot] Warning: could not save task output: {e}")

    # ── Phase 3: Review (with iterative fix loop) ────────────────────────
    print(f"\n## Phase 3/4: Integration Review (up to {MAX_ITERATIONS} fix cycles)\n")
    output_parts.append(f"\n## Phase 3/4: Integration Review\n")

    all_code = "\n\n".join(
        f"### Task {tid} Output\n{result}"
        for tid, result in task_results.items()
    )

    ckpt = load_checkpoint(ckpt_id, "review")
    if ckpt:
        print(f"  [Checkpoint] Restored from review checkpoint")
        review_output = ckpt.get("data", {}).get("review_output", "")
        final_code = ckpt.get("data", {}).get("final_code", "")
        passed = ckpt.get("data", {}).get("passed", False)
    else:
        print("  [Reviewer] Scanning rule docs...")
        review_files = find_relevant_files("rules review", "review")
        review_context = format_file_context(review_files)

        review_output, final_code, passed = review_with_iterations(
            user_prompt=user_prompt,
            director_output=director_output,
            all_code=all_code,
            review_context=review_context,
        )

        # Save checkpoint
        save_checkpoint(ckpt_id, "review", {
            "review_output": review_output,
            "final_code": final_code,
            "passed": passed,
        })

    output_parts.append(f"### Review Verdict: {'PASS' if passed else 'FAIL (max cycles reached)'}\n")
    output_parts.append(review_output)

    if final_code != all_code:
        output_parts.append("\n### Corrected Code (after review fixes)\n")
        output_parts.append(final_code)

    # ── Phase 4: Director Final Approval ─────────────────────────────────
    print("\n## Phase 4/4: Director — Final Approval\n")
    output_parts.append("\n## Phase 4/4: Director — Final Approval\n")

    code_for_approval = final_code if final_code != all_code else all_code

    ckpt = load_checkpoint(ckpt_id, "final_approval")
    if ckpt:
        print(f"  [Checkpoint] Restored from final approval checkpoint")
        final_output = ckpt.get("data", {}).get("final_output", "")
    else:
        final_output = call_ollama(
            FINAL_APPROVAL_SYSTEM,
            f"## Original Feature Request\n{user_prompt}\n\n"
            f"## Your Task Breakdown\n{director_output}\n\n"
            f"## Generated Code\n{code_for_approval}\n\n"
            f"## Integration Review Report\n{review_output}\n\n"
            f"Review the complete output above. "
            f"State **APPROVED** if everything is satisfactory, "
            f"or **REVISION REQUIRED** with specific changes needed.",
            "Director (Final Approval)",
            model=DIRECTOR_MODEL,
        )
        output_parts.append(final_output)

        # Save checkpoint
        save_checkpoint(ckpt_id, "final_approval", {
            "final_output": final_output,
        })

    # ── Summary ──────────────────────────────────────────────────────────
    summary = (
        f"\n---\n## Pipeline Complete\n"
        f"**Checkpoint ID:** {ckpt_id}\n"
        f"**Tasks executed:** {len(tasks)}\n"
        f"**Max iterations per task:** {MAX_ITERATIONS}\n"
        f"**Review verdict:** {'PASS' if passed else 'FAIL'}\n"
        f"**Final approval:** See Phase 4 above\n"
        f"**Resume with:** python pipeline.py --checkpoint {ckpt_id} \"{user_prompt}\"\n"
    )
    print(summary)
    output_parts.append(summary)

    return "\n".join(output_parts)


# ── Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pipeline.py [--checkpoint <id>] \"your feature request\"")
        print("Example: python pipeline.py \"add a jackpot to the plinko attraction\"")
        print("Example: python pipeline.py --checkpoint 20260429_123456 \"continue from checkpoint\"")
        sys.exit(1)

    checkpoint_id = None
    args = sys.argv[1:]

    if "--checkpoint" in args:
        idx = args.index("--checkpoint")
        if idx + 1 < len(args):
            checkpoint_id = args[idx + 1]
            args = args[:idx] + args[idx + 2:]

    user_prompt = " ".join(args)
    full_output = run_pipeline(user_prompt, checkpoint_id)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"pipeline_output_{timestamp}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(full_output)
    print(f"\nFull output saved to: {filename}")
