"""Minimal focused fix for mesh_loops.py: Tasks 2,4,5 only"""
from pathlib import Path
p = Path("C:/Users/Admin/source/repos/midway-pipeline/mesh_loops.py")
text = p.read_text("utf-8")

# ── Task 2: Reorder - Move Phase 1+2 BEFORE Auto-Feeder and Phase 0.5 ──
# The current order (lines 79-154): Phase 0.5 (Auto-Feeder + Scope Gate)
# The desired order: Guard → Phase 1 → Phase 2 → AutoFetch → AutoFeeder → Phase 0.5

# Step 1: Extract the old Phase 0.5 block (lines 79-154) and Phase 1+2 (lines 156-188)
# Step 2: Build new ordering

lines = text.splitlines(keepends=True)

# Extract section A: Phase 0.5 header + auto_feeder variables + guard + scope gate (lines 79-154)
section_a = ''.join(lines[78:154])  # 0-indexed, exclusive at 154 (line 155 is blank)

# Extract section B: Phase 1 GDD + Phase 2 + AutoFetch (lines 156-188)
section_b = ''.join(lines[155:189])  # exclusive at 189 (line 189 blank before Director)

# Extract the guard-read-only-check from inside section_a's "else:" block
# We need to pull just the Declare_Guard part (read_only_keywords through is_read_only_question)
# and the Scope Gate logic (lines 107-154)

# Build new ordering:
# Guard (read_only_keywords + is_read_only_question definition)
# Phase 1 (GDD)
# Phase 2 (Project State)
# Auto-Fetch
# Auto-Feeder
# Phase 0.5 (Scope Gate)
# Then keep Phase 3 (Director) from line 190 onward

# Extract just the read_only_keywords and is_read_only_question definition from the guard
# (without the if/else wrapper from the old code)
guard_def = """    # ── Defensive Guard: Detect read-only / informational prompts ──
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
    # If the prompt ends with '?' it's a question — never blueprint it
    is_read_only_question = (
        prompt_lower.endswith("?")
        or any(prompt_lower.startswith(kw) for kw in read_only_keywords)
    )

"""

# New combined block replacing lines 79-188
new_block = """    blueprint_path = ctx.project_root / "docs" / "project_blueprint.md"

    # Define words that explicitly trigger the Auto-Feeder to pull the next task
    auto_feed_triggers = {"continue", "next", "proceed", "c", "go", "next task"}

    is_auto_feed_request = (
        not ctx.user_prompt
        or ctx.user_prompt.strip().lower() in auto_feed_triggers
    )

    # ── Defensive Guard: Detect read-only / informational prompts ──
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
    # If the prompt ends with '?' it's a question — never blueprint it
    is_read_only_question = (
        prompt_lower.endswith("?")
        or any(prompt_lower.startswith(kw) for kw in read_only_keywords)
    )

    if is_auto_feed_request and not blueprint_path.is_file():
        print("  [ERROR] No prompt provided and no blueprint found.")
        ctx.final_output = "Failed to start."
        return ctx

    # ── Phase 1: GDD Librarian (always runs — gathers design context) ──────
    print(f"\\n{'='*70}")
    print(f"  Phase 1: GDD Librarian")
    print(f"{'='*70}")
    ctx.output_parts.append("\\n## Phase 1: GDD Librarian\\n")

    ctx.gdd_context = recursive_librarian(ctx.user_prompt)
    if ctx.gdd_context:
        ctx.output_parts.append(ctx.gdd_context + "\\n")
    else:
        ctx.output_parts.append("No relevant GDD sections found.\\n")

    # ── Phase 2: Project State & File Context (always runs) ────────────
    print(f"\\n{'='*70}")
    print(f"  Phase 2: Project Context")
    print(f"{'='*70}")
    ctx.output_parts.append("\\n## Phase 2: Project Context\\n")

    ctx.project_state = get_project_state()
    ctx.output_parts.append(ctx.project_state + "\\n")

    ctx.structure = curate_project_structure(ctx.user_prompt)
    ctx.output_parts.append(ctx.structure + "\\n")

    # ── Auto-Fetch Referenced Files ───────────────────────────────────
    refs = parse_file_references(ctx.user_prompt)
    refs_block = fetch_referenced_files(refs)
    set_referenced_files_cache(refs_block)
    if refs_block:
        ctx.output_parts.append(
            "### Referenced Files (auto-injected)\\n" + refs_block + "\\n"
        )
        print(f"  [AutoRef] {len(refs)} file reference(s) parsed and cached for all agents")

    # ── Auto-Feeder: extract next task from blueprint (for auto-feed requests) ──
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

    # ── Phase 0.5: Lead Producer (Scope Gate) — only for fresh prompts ──
    # Runs AFTER GDD/Project State gathering so the model can make informed decisions.
    if not is_auto_feed_request:
        gdd_snippet = ctx.gdd_context[:2000] if ctx.gdd_context else "(no GDD context)"
        state_snippet = ctx.project_state[:2000] if ctx.project_state else "(no project state)"
        if is_read_only_question:
            print(f"\\n  [Lead Producer] Prompt looks like a read-only question. "
                  f"Passing through to Phase 1 (Librarian) instead of blueprint generation.")
            print(f"  [Lead Producer] Prompt: {ctx.user_prompt[:80]}")
        else:
            scope_prompt = (
                f"Analyze this prompt: '{ctx.user_prompt}'.\\n\\n"
                f"## Relevant GDD Context\\n{gdd_snippet}\\n\\n"
                f"## Current Project State\\n{state_snippet}\\n\\n"
                f"Consider whether it requires modifying >{SCOPE_FILE_LIMIT} files or "
                f"writing >{SCOPE_LINE_LIMIT} lines. Think step by step, then conclude "
                f"with [VERDICT: NARROW] or [VERDICT: TOO_BROAD]."
            )
            scope_eval = call_ollama(
                "You are a Lead Producer.", scope_prompt, "Scope Gate", REASONING_MODEL
            )

            verdict_match = re.search(r"[[]VERDICT:[ \t]*TOO_BROAD[]]", scope_eval, re.IGNORECASE)
            if verdict_match:
                print(f"\\n  [Lead Producer] Scope is TOO_BROAD. Generating blueprint...")
                unavailable_text = get_unavailable_domains_text()
                hard_constraints = (
                    f"HARD CONSTRAINTS — Do NOT plan for:\\n"
                    f"{unavailable_text}\\n\\n"
                    f"This is a custom engine using SDL2/OpenGL/Jolt/Box2D/Lua. "
                    f"Never reference Unreal, Unity, Godot, or proprietary engines. "
                    f"If the user asks for features from unavailable domains, "
                    f"substitute with wireframes, debug logging, or standard placeholders."
                )
                blueprint_prompt = (
                    f"Create a step-by-step markdown blueprint to accomplish: "
                    f"{ctx.user_prompt}.\\n\\n"
                    f"{hard_constraints}\\n\\n"
                    f"## GDD Context\\n"
                    f"{ctx.gdd_context[:3000] if ctx.gdd_context else '(none)'}\\n\\n"
                    f"## Current Project State\\n"
                    f"{ctx.project_state[:2000] if ctx.project_state else '(none)'}\\n\\n"
                    f"## Unavailable Domains\\n"
                    f"{unavailable_text}\\n\\n"
                    f"## Project Structure\\n"
                    f"{ctx.structure[:2000] if ctx.structure else '(none)'}\\n\\n"
                    f"Base your step-by-step tasks strictly on the provided GDD "
                    f"and current project state. "
                    f"Do NOT include tasks for unavailable domains. "
                    f"Base tasks strictly on the provided GDD and current project state.\\n\\n"
                    f"Format as a checklist:\\n"
                    f"'- [ ] Task 1: ...'"
                )
                blueprint = call_ollama(
                    "You are a Lead Producer.", blueprint_prompt,
                    "Blueprint Generation", REASONING_MODEL
                )
                blueprint_path.parent.mkdir(exist_ok=True)
                blueprint_path.write_text(blueprint, encoding="utf-8")
                print(f"  [Lead Producer] Saved to docs/project_blueprint.md.")

                # ── Continuous Execution: extract first task & fall through ──
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
                    print("  [Lead Producer] Blueprint generated but no tasks found — continuing with original prompt.")

"""

# Replace lines 79-188 with the new combined block
old_section = ''.join(lines[78:189])
if old_section in text:
    text = text.replace(old_section, new_block, 1)
    print("[OK] Tasks 2+4+5: Phase reordering, verdict regex, continuous execution all applied")
else:
    print("[FAIL] Could not find old section to replace")

# Clean up old Phase 1/2 comments if they left stray markers
# (they should be gone since the whole section was replaced)

p.write_text(text, "utf-8")
print("[OK] mesh_loops.py saved")
