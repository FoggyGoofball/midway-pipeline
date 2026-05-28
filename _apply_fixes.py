#!/usr/bin/env python3
# Apply 5 critical architectural fixes to the TRUE workspace
import os, re
from pathlib import Path

TRUE_ROOT = Path("C:/Users/Admin/source/repos/midway-pipeline")

def fix_file(path, label, replacements):
    text = path.read_text(encoding="utf-8")
    changes = 0
    for old, new in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            changes += 1
    path.write_text(text, encoding="utf-8")
    status = "OK" if changes > 0 else "WARN"
    print(f"[{status}] {label}: {changes} replacement(s)")

# ===== TASK 1: Hard-Anchor Project Directory =====

# pipeline.py - _CTX singleton
p = TRUE_ROOT / "pipeline.py"
fix_file(p, "Task 1a: pipeline.py _CTX", [
    (
        '_CTX = PipelineContext(\n    project_root=Path(__file__).resolve().parent.parent / "midway",\n    memory_dir=Path(__file__).resolve().parent.parent / "midway" / "docs" / "memory",',
        '_CTX = PipelineContext(\n    project_root=Path(os.getenv("MIDWAY_PROJECT_ROOT", Path(__file__).resolve().parent.with_name("midway"))),\n    memory_dir=Path(os.getenv("MIDWAY_PROJECT_ROOT", Path(__file__).resolve().parent.with_name("midway"))) / "docs" / "memory",'
    ),
])

# pipeline.py - module-level PROJECT_ROOT
fix_file(p, "Task 1b: pipeline.py PROJECT_ROOT", [
    (
        'PROJECT_ROOT = Path(__file__).resolve().parent.parent / "midway"',
        'PROJECT_ROOT = Path(os.getenv("MIDWAY_PROJECT_ROOT", Path(__file__).resolve().parent.with_name("midway")))'
    ),
])

# _pipeline_helpers.py
p = TRUE_ROOT / "_pipeline_helpers.py"
fix_file(p, "Task 1c: _pipeline_helpers.py PROJECT_ROOT", [
    (
        'PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent / "midway"',
        'PROJECT_ROOT: Path = Path(os.getenv("MIDWAY_PROJECT_ROOT", Path(__file__).resolve().parent.with_name("midway")))'
    ),
])

# pipeline_stream_server.py
p = TRUE_ROOT / "pipeline_stream_server.py"
fix_file(p, "Task 1d: pipeline_stream_server.py PROJECT_ROOT", [
    (
        "PROJECT_ROOT = Path(__file__).parent.resolve()",
        'PROJECT_ROOT = Path(os.getenv("MIDWAY_PROJECT_ROOT", Path(__file__).resolve().parent.with_name("midway")))'
    ),
])


# ===== TASKS 2-5: mesh_loops.py =====

p = TRUE_ROOT / "mesh_loops.py"
text = p.read_text(encoding="utf-8")

old_section = '''    # -- Phase 0.5: Lead Producer (Scope Gate & Auto-Feeder) ---------------
    blueprint_path = ctx.project_root / "docs" / "project_blueprint.md"

    # Define words that explicitly trigger the Auto-Feeder to pull the next task
    auto_feed_triggers = {"continue", "next", "proceed", "c", "go", "next task"}
    
    is_auto_feed_request = (
        not ctx.user_prompt 
        or ctx.user_prompt.strip().lower() in auto_feed_triggers
    )

    if is_auto_feed_request:
        if blueprint_path.is_file():
            content = blueprint_path.read_text(encoding="utf-8")
            match = re.search(r"- [[] []] (Task [0-9]+: .+)", content)
            if match:
                ctx.user_prompt = match.group(1)
                print(f"  [Lead Producer] Auto-feeding next task: {ctx.user_prompt}")
                new_content = content.replace(f"- [ ] {ctx.user_prompt}", f"- [x] {ctx.user_prompt}", 1)
                blueprint_path.write_text(new_content, encoding="utf-8")
            else:
                print("  [Lead Producer] Blueprint complete. Nothing to do.")
                ctx.final_output = "Blueprint complete."
                return ctx
        else:
            print("  [ERROR] No prompt provided and no blueprint found.")
            ctx.final_output = "Failed to start."
            return ctx
    else:
        # -- Defensive Guard: Detect read-only / informational prompts --
        # Even if the INFORMATIONAL classifier miscategorized, the Scope Gate
        # should NEVER route a read-only question to the Lead Producer.
        read_only_keywords = [
            "how is", "what is", "explain", "summarize", "status",
            "progress", "tell me about", "describe", "list",
            "show me", "overview", "what are", "how does",
            "what does", "can you tell", "information about",
            "context on", "update on", "report on"
        ]
        prompt_lower = ctx.user_prompt.lower().strip()
        # If the prompt ends with '?' it's a question -- never blueprint it
        is_read_only_question = (
            prompt_lower.endswith("?")
            or any(prompt_lower.startswith(kw) for kw in read_only_keywords)
        )
        if is_read_only_question:
            print(f"\n  [Lead Producer] Prompt looks like a read-only question. "
                  f"Passing through to Phase 1 (Librarian) instead of blueprint generation.")
            print(f"  [Lead Producer] Prompt: {ctx.user_prompt[:80]}")
        else:
            scope_prompt = (
                f"Analyze this prompt: '{ctx.user_prompt}'. "
                f"If it requires modifying >{SCOPE_FILE_LIMIT} files or writing "
                f">{SCOPE_LINE_LIMIT} lines, respond strictly with 'TOO_BROAD'. "
                f"Otherwise respond 'NARROW'."
            )
            scope_eval = call_ollama(
                "You are a Lead Producer.", scope_prompt, "Scope Gate", REASONING_MODEL
            )

            if "TOO_BROAD" in scope_eval.upper():
                print(f"\n  [Lead Producer] Scope is TOO BROAD. Generating blueprint...")
                blueprint_prompt = (
                    f"Create a step-by-step markdown blueprint to accomplish: "
                    f"{ctx.user_prompt}. Format as a checklist: "
                    f"'- [ ] Task 1: ...'"
                )
                blueprint = call_ollama(
                    "You are a Lead Producer.", blueprint_prompt,
                    "Blueprint Generation", REASONING_MODEL
                )
                blueprint_path.parent.mkdir(exist_ok=True)
                blueprint_path.write_text(blueprint, encoding="utf-8")
                print(f"  [Lead Producer] Saved to docs/project_blueprint.md.")
                ctx.final_output = "Blueprint created. Run pipeline with no prompt to execute Task 1."
                return ctx

    # -- Phase 1: GDD Librarian -------------------------------------------
    print(f"\n{'='*70}")
    print(f"  Phase 1: GDD Librarian")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 1: GDD Librarian\n")

    ctx.gdd_context = recursive_librarian(ctx.user_prompt)
    if ctx.gdd_context:
        ctx.output_parts.append(ctx.gdd_context + "\n")
    else:
        ctx.output_parts.append("No relevant GDD sections found.\n")

    # -- Phase 2: Project State & File Context ----------------------------
    print(f"\n{'='*70}")
    print(f"  Phase 2: Project Context")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 2: Project Context\n")

    ctx.project_state = get_project_state()
    ctx.output_parts.append(ctx.project_state + "\n")

    ctx.structure = curate_project_structure(ctx.user_prompt)
    ctx.output_parts.append(ctx.structure + "\n")

    # -- Auto-Fetch Referenced Files ------------------------------------------
    refs = parse_file_references(ctx.user_prompt)
    refs_block = fetch_referenced_files(refs)
    set_referenced_files_cache(refs_block)
    if refs_block:
        ctx.output_parts.append(
            "### Referenced Files (auto-injected)\n" + refs_block + "\n"
        )
        print(f"  [AutoRef] {len(refs)} file reference(s) parsed and cached for all agents")'''

new_section = '''    blueprint_path = ctx.project_root / "docs" / "project_blueprint.md"

    # Define words that explicitly trigger the Auto-Feeder to pull the next task
    auto_feed_triggers = {"continue", "next", "proceed", "c", "go", "next task"}

    is_auto_feed_request = (
        not ctx.user_prompt
        or ctx.user_prompt.strip().lower() in auto_feed_triggers
    )

    # -- Read-Only/Informational Guard -------------------------------------
    read_only_keywords = [
        "how is", "what is", "explain", "summarize", "status",
        "progress", "tell me about", "describe", "list",
        "show me", "overview", "what are", "how does",
        "what does", "can you tell", "information about",
        "context on", "update on", "report on"
    ]
    prompt_lower = ctx.user_prompt.lower().strip()
    is_read_only_question = (
        prompt_lower.endswith("?")
        or any(prompt_lower.startswith(kw) for kw in read_only_keywords)
    )

    if is_auto_feed_request and not blueprint_path.is_file():
        print("  [ERROR] No prompt provided and no blueprint found.")
        ctx.final_output = "Failed to start."
        return ctx

    # -- Phase 1: GDD Librarian (always runs - gathers design context) -----
    print(f"\n{'='*70}")
    print(f"  Phase 1: GDD Librarian")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 1: GDD Librarian\n")
    ctx.gdd_context = recursive_librarian(ctx.user_prompt)
    if ctx.gdd_context:
        ctx.output_parts.append(ctx.gdd_context + "\n")
    else:
        ctx.output_parts.append("No relevant GDD sections found.\n")

    # -- Phase 2: Project State & File Context (always runs) --------------
    print(f"\n{'='*70}")
    print(f"  Phase 2: Project Context")
    print(f"{'='*70}")
    ctx.output_parts.append("\n## Phase 2: Project Context\n")
    ctx.project_state = get_project_state()
    ctx.output_parts.append(ctx.project_state + "\n")
    ctx.structure = curate_project_structure(ctx.user_prompt)
    ctx.output_parts.append(ctx.structure + "\n")

    # -- Auto-Fetch Referenced Files -------------------------------------
    refs = parse_file_references(ctx.user_prompt)
    refs_block = fetch_referenced_files(refs)
    set_referenced_files_cache(refs_block)
    if refs_block:
        ctx.output_parts.append(
            "### Referenced Files (auto-injected)\n" + refs_block + "\n"
        )
        print(f"  [AutoRef] {len(refs)} file reference(s) parsed and cached for all agents")

    # -- Auto-Feeder: extract next task from blueprint (for auto-feed requests) --
    if is_auto_feed_request:
        if blueprint_path.is_file():
            content = blueprint_path.read_text(encoding="utf-8")
            match = re.search(
                r"^[-*]?[ \t]*[[] []][ \t]*(?:Task [0-9]+:[ \t]*)?(.+)",
                content, re.MULTILINE
            )
            if match:
                raw_line = match.group(0)
                task_text = match.group(1)
                ctx.user_prompt = task_text.strip()
                print(f"  [Lead Producer] Auto-feeding next task: {task_text.strip()}")
                new_content = content.replace(raw_line, raw_line.replace("[ ]", "[x]", 1), 1)
                blueprint_path.write_text(new_content, encoding="utf-8")
            else:
                print("  [Lead Producer] Blueprint complete. Nothing to do.")
                ctx.final_output = "Blueprint complete."
                return ctx

    # -- Phase 0.5: Lead Producer (Scope Gate) - only for fresh prompts ----
    # Runs AFTER GDD/Project State gathering so the model can make informed decisions.
    if not is_auto_feed_request:
        gdd_snippet = ctx.gdd_context[:2000] if ctx.gdd_context else "(no GDD context)"
        state_snippet = ctx.project_state[:2000] if ctx.project_state else "(no project state)"
        if is_read_only_question:
            print(f"\n  [Lead Producer] Prompt looks like a read-only question. "
                  f"Passing through to Phase 1 (Librarian) instead of blueprint generation.")
            print(f"  [Lead Producer] Prompt: {ctx.user_prompt[:80]}")
        else:
            scope_prompt = (
                f"Analyze this prompt: '{ctx.user_prompt}'.\n\n"
                f"## Relevant GDD Context\n{gdd_snippet}\n\n"
                f"## Current Project State\n{state_snippet}\n\n"
                f"Consider whether it requires modifying >{SCOPE_FILE_LIMIT} files or "
                f"writing >{SCOPE_LINE_LIMIT} lines. Think step by step, then conclude "
                f"with [VERDICT: NARROW] or [VERDICT: TOO_BROAD]."
            )
            scope_eval = call_ollama(
                "You are a Lead Producer.", scope_prompt, "Scope Gate", REASONING_MODEL
            )
            verdict_match = re.search(r"[[]VERDICT:[ \t]*TOO_BROAD[]]", scope_eval, re.IGNORECASE)
            if verdict_match:
                print(f"\n  [Lead Producer] Scope is TOO_BROAD. Generating blueprint...")
                unavailable_text = get_unavailable_domains_text()
                hard_constraints = (
                    f"HARD CONSTRAINTS - Do NOT plan for:\n"
                    f"{unavailable_text}\n\n"
                    f"This is a custom engine using SDL2/OpenGL/Jolt/Box2D/Lua. "
                    f"Never reference Unreal, Unity, Godot, or proprietary engines. "
                    f"If the user asks for features from unavailable domains, "
                    f"substitute with wireframes, debug logging, or standard placeholders."
                )
                blueprint_prompt = (
                    f"Create a step-by-step markdown blueprint to accomplish: "
                    f"{ctx.user_prompt}.\n\n"
                    f"{hard_constraints}\n\n"
                    f"## GDD Context\n"
                    f"{ctx.gdd_context[:3000] if ctx.gdd_context else '(none)'}\n\n"
                    f"## Current Project State\n"
                    f"{ctx.project_state[:2000] if ctx.project_state else '(none)'}\n\n"
                    f"## Unavailable Domains\n"
                    f"{unavailable_text}\n\n"
                    f"## Project Structure\n"
                    f"{ctx.structure[:2000] if ctx.structure else '(none)'}\n\n"
                    f"Base your step-by-step tasks strictly on the provided GDD "
                    f"and current project state. "
                    f"Do NOT include tasks for unavailable domains. "
                    f"Base tasks strictly on the provided GDD and current project state.\n\n"
                    f"Format as a checklist:\n"
                    f"'- [ ] Task 1: ...'"
                )
                blueprint = call_ollama(
                    "You are a Lead Producer.", blueprint_prompt,
                    "Blueprint Generation", REASONING_MODEL
                )
                blueprint_path.parent.mkdir(exist_ok=True)
                blueprint_path.write_text(blueprint, encoding="utf-8")
                print(f"  [Lead Producer] Saved to docs/project_blueprint.md.")

                # -- Continuous Execution: extract first task & fall through --
                content = blueprint_path.read_text(encoding="utf-8")
                first_match = re.search(
                    r"^[-*]?[ \t]*[[] []][ \t]*(?:Task [0-9]+:[ \t]*)?(.+)",
                    content, re.MULTILINE
                )
                if first_match:
                    raw_line = first_match.group(0)
                    task_text = first_match.group(1).strip()
                    ctx.user_prompt = task_text
                    new_content = content.replace(raw_line, raw_line.replace("[ ]", "[x]", 1), 1)
                    blueprint_path.write_text(new_content, encoding="utf-8")
                    print(f"  [Lead Producer] Auto-feeding first task: {task_text}")
                    print(f"  [Lead Producer] Continuing to Phase 3...")
                else:
                    print("  [Lead Producer] Blueprint generated but no tasks found - continuing with original prompt.")'''

if old_section in text:
    text = text.replace(old_section, new_section, 1)
    print("[OK] Task 2: Reordered run_fetches (GDD/Project State before Scope Gate)")
else:
    print("[WARN] Task 2: Could not find original section")

# -- Import get_unavailable_domains_text --
old_import_end = '''    generate_failure_report,
)'''
new_import_end = '''    generate_failure_report,
    get_unavailable_domains_text,
)'''
if old_import_end in text:
    text = text.replace(old_import_end, new_import_end, 1)
    print("[OK] Task 3: Added get_unavailable_domains_text to imports")
else:
    if "get_unavailable_domains_text" in text:
        print("[WARN] Task 3: Import already present")
    else:
        print("[WARN] Task 3: Could not find import block")

p.write_text(text, encoding="utf-8")
print("[OK] mesh_loops.py saved")

# ===== VERIFICATION =====
print("\n" + "-"*60)
print("VERIFICATION")
print("-"*60)

for fp, label in [
    (TRUE_ROOT / "pipeline.py", "pipeline.py"),
    (TRUE_ROOT / "_pipeline_helpers.py", "_pipeline_helpers.py"),
    (TRUE_ROOT / "pipeline_stream_server.py", "pipeline_stream_server.py"),
]:
    t = fp.read_text(encoding="utf-8")
    if 'os.getenv("MIDWAY_PROJECT_ROOT"' in t:
        print(f"[PASS 1] {label}: uses MIDWAY_PROJECT_ROOT env var")
    else:
        print(f"[FAIL 1] {label}: does NOT use MIDWAY_PROJECT_ROOT")

t = (TRUE_ROOT / "mesh_loops.py").read_text(encoding="utf-8")

if "Phase 1: GDD Librarian" in t[:t.find("Phase 0.5: Lead Producer")]:
    print("[PASS 2] GDD Librarian runs BEFORE Scope Gate")
else:
    print("[FAIL 2] GDD Librarian incorrectly positioned")

if "get_unavailable_domains_text()" in t:
    print("[PASS 3] blueprint_prompt references get_unavailable_domains_text()")
if 'Do NOT include tasks for unavailable domains' in t:
    print("[PASS 3] blueprint has anti-hallucination instruction")

# Check for regex verdict match
has_regex = False
for line in t.splitlines():
    if 're.search' in line and 'VERDICT' in line:
        has_regex = True
        break
if has_regex:
    print("[PASS 4] Uses re.search for [VERDICT: TOO_BROAD]")
else:
    print("[FAIL 4] Missing re.search for VERDICT")

if 'if verdict_match:' in t:
    print("[PASS 4] Uses if verdict_match: instead of string containment")

# Check for no return ctx after blueprint save
bp_idx = t.find("blueprint_path.write_text(blueprint")
if bp_idx > -1:
    search_window = t[bp_idx:bp_idx+200]
    if "return ctx" in search_window:
        print("[FAIL 5] return ctx still present after blueprint save!")
    else:
        print("[PASS 5] No return ctx after blueprint save (continuous execution active)")
else:
    print("[WARN 5] Could not find blueprint save location to verify")

# Check for relaxed regex
for line in t.splitlines():
    if 'r"' in line and '[-*]' in line and '[[] []]' in line:
        print("[PASS 5] Auto-feed regex is relaxed (no trailing $)")
        break

print("\n[DONE] All fixes applied to TRUE workspace: C:/Users/Admin/source/repos/midway-pipeline/")
