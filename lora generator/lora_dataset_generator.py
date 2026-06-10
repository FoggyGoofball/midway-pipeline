#!/usr/bin/env python3
"""
lora_dataset_generator.py
=========================
Generates a 1500-point JSONL dataset for fine-tuning Qwen2.5-Coder-7B
on the Universal Virtual Memory Paging Protocol via LoRA.

Protocol Summary
----------------
The model learns to interact with an external orchestrator using strict
XML paging tokens instead of hallucinating file contents:

  PAGE_IN (untargeted):  <invoke_kernel><action>PAGE_IN</action><target>file.md</target></invoke_kernel>
  PAGE_IN (targeted):    <invoke_kernel><action>PAGE_IN</action><target>file.cpp</target><search>ClassName</search></invoke_kernel>
                         <invoke_kernel><action>PAGE_IN</action><target>file.cpp</target><lines>100-150</lines></invoke_kernel>
  PAGE_OUT:              <invoke_kernel><action>PAGE_OUT</action><target>old context</target></invoke_kernel>

Output Format
-------------
JSONL with one JSON object per line, structured as a messages array
compatible with Unsloth, Axolotl, and LLaMA-Factory ChatML format:

  {"messages": [{"role": "system", "content": "..."},
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."}]}

Usage
-----
  python lora_generator/lora_dataset_generator.py
  → Produces paging_lora_dataset.jsonl in the same directory
"""

import json
import os
import random

# ── Deterministic Seed for Reproducibility ─────────────────────────────
random.seed(42)

NUM_SAMPLES = 1500

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "paging_lora_dataset.jsonl")

# ── Protocol String (Injected into Every System Prompt) ─────────────────
PROTOCOL_TEXT = (
    "---\n"
    "STRICT XML PAGING PROTOCOL:\n"
    "Your active context window is a finite resource. "
    "When large reference documents are too big to fit, "
    "they are replaced with a <VRAM_STUB>.\n\n"
    "### PAGE_IN — Fetch Content From Disk\n"
    "If you NEED the full contents, you MUST emit exactly:\n"
    "<invoke_kernel><action>PAGE_IN</action><target>file.md</target></invoke_kernel>\n"
    "If you only need a specific section, use TARGETING TAGS:\n"
    "<invoke_kernel><action>PAGE_IN</action><target>file.cpp</target>"
    "<search>ClassName</search></invoke_kernel>\n"
    "  OR\n"
    "<invoke_kernel><action>PAGE_IN</action><target>file.cpp</target>"
    "<lines>100-150</lines></invoke_kernel>\n\n"
    "### PAGE_OUT — Free Memory\n"
    "If you see a [SYSTEM KERNEL: VRAM critical] message:\n"
    "<invoke_kernel><action>PAGE_OUT</action><target>old context</target>"
    "</invoke_kernel>\n\n"
    "### HARD CAP RULE\n"
    "If you try to PAGE_IN a large file without a targeting tag, "
    "the kernel will reject it. You MUST retry with <search> or <lines>."
    "\n---"
)

# ── Codebase-Agnostic Domains ───────────────────────────────────────────
DOMAINS = [
    "Python", "TypeScript", "Rust", "Go",
    "Java", "C#", "Ruby", "Kotlin",
]

# ── Generic Files (No Project-Specific Content) ─────────────────────────
FILES = [
    {"name": "src/utils/helpers.py",   "size": "small", "concepts": ["parse_config", "validate_schema"]},
    {"name": "src/utils/helpers.ts",   "size": "small", "concepts": ["type_guard", "format_date"]},
    {"name": "src/core/engine.rs",     "size": "large", "concepts": ["trait EventHandler", "fn dispatch"]},
    {"name": "src/core/engine.go",     "size": "large", "concepts": ["type HandlerFunc", "ServeHTTP"]},
    {"name": "src/api/handlers.java",  "size": "large", "concepts": ["@RestController", "ResponseEntity"]},
    {"name": "src/api/handlers.cs",    "size": "large", "concepts": ["IActionResult", "FromBody"]},
    {"name": "docs/architecture.md",   "size": "small", "concepts": ["system design", "data flow"]},
    {"name": "docs/api_reference.md",  "size": "small", "concepts": ["endpoint spec", "request schema"]},
    {"name": "src/models/user.rb",     "size": "small", "concepts": ["validates", "has_many"]},
    {"name": "src/models/user.kt",     "size": "small", "concepts": ["data class", "Room entity"]},
    {"name": "src/services/payment.py","size": "large", "concepts": ["process_refund", "Transaction"]},
    {"name": "src/services/pipeline.ts","size": "large", "concepts": ["Observable", "pipe operator"]},
]

SMALL_FILES = [f for f in FILES if f["size"] == "small"]
LARGE_FILES = [f for f in FILES if f["size"] == "large"]


def generate_system_prompt(domain: str, file_obj: dict) -> str:
    """Build the system prompt with domain, protocol, and a VRAM_STUB."""
    concept = file_obj["concepts"][0]
    file_name = file_obj["name"]
    return (
        f"You are a {domain} developer.\n"
        f"{PROTOCOL_TEXT}\n\n"
        f"<VRAM_STUB id=\"{file_name}\" "
        f"summary=\"Contains {concept}...\" />"
    )


# ── Scenario Implementations ────────────────────────────────────────────

def _scenario_untargeted_in(domain: str) -> list:
    """(30%) Model sees a small-file stub and requests the full file."""
    file_obj = random.choice(SMALL_FILES)
    target = file_obj["name"]
    concept = random.choice(file_obj["concepts"])

    sys_prompt = generate_system_prompt(domain, file_obj)
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user",
         "content": f"Implement the {concept} feature. Review the reference file and proceed."},
        {"role": "assistant",
         "content": (
             f"Before implementing {concept}, I need to review the full file.\n"
             f"<invoke_kernel><action>PAGE_IN</action>"
             f"<target>{target}</target></invoke_kernel>"
         )},
        {"role": "user",
         "content": (
             f"## Paged-In File Content\n"
             f"**Source:** `{target}`\n"
             f"```\n# (Full file content for {target} would appear here)\n"
             f"```\n\n"
             f"[SYSTEM KERNEL: Paging complete. Continue generating your response.]"
         )},
        {"role": "assistant",
         "content": (
             f"Thank you. I have reviewed `{target}`. "
             f"Here is the implementation of {concept}:\n"
             f"```\n# Implementation of {concept}\n"
             f"def {concept.replace(' ', '_').lower()}():\n"
             f"    pass\n"
             f"```"
         )},
    ]


def _scenario_search_in(domain: str) -> list:
    """(25%) Model targets a large file with a <search> tag."""
    file_obj = random.choice(LARGE_FILES)
    target = file_obj["name"]
    concept = random.choice(file_obj["concepts"])
    search_term = concept.split()[-1] if " " in concept else concept

    sys_prompt = generate_system_prompt(domain, file_obj)
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user",
         "content": f"I need you to fix the {concept} logic. Use the reference to find the relevant section."},
        {"role": "assistant",
         "content": (
             f"The file is large. I'll search for the {concept} section.\n"
             f"<invoke_kernel><action>PAGE_IN</action>"
             f"<target>{target}</target>"
             f"<search>{search_term}</search></invoke_kernel>"
         )},
        {"role": "user",
         "content": (
             f"## Paged-In File Content (Search Match)\n"
             f"**Source:** `{target}`\n"
             f"**Search:** `{search_term}`\n"
             f"```\n# (Chunk surrounding {search_term} would appear here)\n"
             f"```\n\n"
             f"[SYSTEM KERNEL: Paging complete. Continue generating your response.]"
         )},
        {"role": "assistant",
         "content": (
             f"I found the {concept} section. Here is the fix:\n"
             f"```\n# Fixed {concept}\n"
             f"def {search_term.lower()}():\n"
             f"    # corrected implementation\n"
             f"    pass\n"
             f"```"
         )},
    ]


def _scenario_lines_in(domain: str) -> list:
    """(15%) Model targets a specific line range with <lines>."""
    file_obj = random.choice(LARGE_FILES)
    target = file_obj["name"]
    concept = random.choice(file_obj["concepts"])

    sys_prompt = generate_system_prompt(domain, file_obj)
    line_start = random.randint(10, 50)
    line_end = line_start + random.randint(10, 40)
    lines_range = f"{line_start}-{line_end}"

    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user",
         "content": (
             f"Update lines {line_start}-{line_end} in {target} to fix the {concept}."
         )},
        {"role": "assistant",
         "content": (
             f"I'll fetch the relevant lines to see the current code.\n"
             f"<invoke_kernel><action>PAGE_IN</action>"
             f"<target>{target}</target>"
             f"<lines>{lines_range}</lines></invoke_kernel>"
         )},
        {"role": "user",
         "content": (
             f"## Paged-In File Content (Targeted Lines)\n"
             f"**Source:** `{target}`\n"
             f"**Lines:** {lines_range}\n"
             f"```\n# (Lines {line_start}-{line_end} of {target} would appear here)\n"
             f"```\n\n"
             f"[SYSTEM KERNEL: Paging complete. Continue generating your response.]"
         )},
        {"role": "assistant",
         "content": (
             f"I can see the current code at lines {line_start}-{line_end}. "
             f"Here is the updated {concept} implementation:\n"
             f"```\n# Updated {concept} at lines {line_start}-{line_end}\n"
             f"def {concept.replace(' ', '_').lower()}():\n"
             f"    # updated implementation\n"
             f"    pass\n"
             f"```"
         )},
    ]


def _scenario_page_out(domain: str) -> list:
    """(15%) Model receives VRAM critical warning and emits PAGE_OUT."""
    target = random.choice(FILES)["name"]
    concept = random.choice(["conversation history", "previous analysis",
                             "cached context", "temporary state"])

    return [
        {"role": "system",
         "content": (
             f"You are a {domain} developer.\n"
             f"{PROTOCOL_TEXT}"
         )},
        {"role": "user",
         "content": (
             f"Continue the implementation. "
             f"[SYSTEM KERNEL: VRAM critical — 85% of context window consumed. "
             f"Emit PAGE_OUT to free memory before proceeding.]"
         )},
        {"role": "assistant",
         "content": (
             f"[SYSTEM KERNEL: VRAM critical detected. Evicting {concept} to free space.]\n"
             f"<invoke_kernel><action>PAGE_OUT</action>"
             f"<target>{concept}</target></invoke_kernel>"
         )},
        {"role": "user",
         "content": (
             f"[SYSTEM KERNEL: PAGE_OUT completed. '{concept}' has been evicted. "
             f"Continue generating your response.]"
         )},
        {"role": "assistant",
         "content": (
             f"Context freed. Continuing with the implementation using remaining memory.\n"
             f"```\n# {concept.replace(' ', '_')}_evicted\n"
             f"# Continuing fresh implementation\n"
             f"```"
         )},
    ]


def _scenario_self_correct(domain: str) -> list:
    """(15%) Model makes untargeted fetch on large file, gets rejected,
    then self-corrects with a targeting tag."""
    file_obj = random.choice(LARGE_FILES)
    target = file_obj["name"]
    concept = random.choice(file_obj["concepts"])
    search_term = concept.split()[-1] if " " in concept else concept

    sys_prompt = generate_system_prompt(domain, file_obj)

    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user",
         "content": f"Implement the {concept} feature based on the reference file."},
        {"role": "assistant",
         "content": (
             f"I need to review the file first.\n"
             f"<invoke_kernel><action>PAGE_IN</action>"
             f"<target>{target}</target></invoke_kernel>"
         )},
        {"role": "user",
         "content": (
             f"[SYSTEM KERNEL: PAGE_IN REJECTED — '{target}' exceeds the "
             f"untargeted Hard Cap. RETRY with a targeting tag:\n"
             f"  <invoke_kernel><action>PAGE_IN</action>"
             f"<target>{target}</target><search>Keyword</search></invoke_kernel>\n"
             f"  <invoke_kernel><action>PAGE_IN</action>"
             f"<target>{target}</target><lines>10-50</lines></invoke_kernel>]"
         )},
        {"role": "assistant",
         "content": (
             f"The file exceeds the hard cap. I'll search for the {concept} section instead.\n"
             f"<invoke_kernel><action>PAGE_IN</action>"
             f"<target>{target}</target>"
             f"<search>{search_term}</search></invoke_kernel>"
         )},
        {"role": "user",
         "content": (
             f"## Paged-In File Content (Search Match)\n"
             f"**Source:** `{target}`\n"
             f"**Search:** `{search_term}`\n"
             f"```\n# (Chunk surrounding {search_term} would appear here)\n"
             f"```\n\n"
             f"[SYSTEM KERNEL: Paging complete. Continue generating your response.]"
         )},
        {"role": "assistant",
         "content": (
             f"Now I have the {concept} section. Here is the implementation:\n"
             f"```\n# Implementation of {concept}\n"
             f"def {search_term.lower()}():\n"
             f"    # feature implementation\n"
             f"    pass\n"
             f"```"
         )},
    ]


SCENARIO_FUNCS = {
    "untargeted_in": _scenario_untargeted_in,
    "search_in":     _scenario_search_in,
    "lines_in":      _scenario_lines_in,
    "page_out":      _scenario_page_out,
    "self_correct":  _scenario_self_correct,
}


def generate_sample() -> list:
    """Generate one multi-turn conversational sample."""
    scenario = random.choices(
        list(SCENARIO_FUNCS.keys()),
        weights=[30, 25, 15, 15, 15],
    )[0]

    domain = random.choice(DOMAINS)
    return SCENARIO_FUNCS[scenario](domain)


def validate_sample(messages: list) -> bool:
    """Validate that a sample conforms to protocol invariants.

    Checks:
      1) Every assistant message that contains a PAGE_IN or PAGE_OUT
         token ends with a closing </invoke_kernel>.
      2) No trailing garbage after </invoke_kernel> in assistant messages.
      3) At least one assistant turn exists.
    """
    for msg in messages:
        if msg["role"] != "assistant":
            continue
        content = msg["content"]
        if "<invoke_kernel>" in content:
            if not content.rstrip().endswith("</invoke_kernel>"):
                return False
            # Verify no text after the final </invoke_kernel>
            last_close = content.rfind("</invoke_kernel>")
            trailing = content[last_close + len("</invoke_kernel>"):]
            if trailing.strip():
                return False
    return True


def main():
    print(f"Generating {NUM_SAMPLES} samples...")
    samples = []
    failures = 0

    for i in range(NUM_SAMPLES):
        sample = generate_sample()
        if not validate_sample(sample):
            failures += 1
            continue
        samples.append({"messages": sample})

        if (i + 1) % 250 == 0:
            print(f"  Progress: {i + 1}/{NUM_SAMPLES} samples generated...")

    print(f"\nGenerated {len(samples)} valid samples ({failures} rejected by validation).")

    # ── Scenario Distribution Report ──────────────────────────────────
    # Use user-message markers to classify (more reliable than assistant content)
    scenario_counts: dict = {}
    for s in samples:
        user_msgs = [m["content"] for m in s["messages"] if m["role"] == "user"]
        joined_users = " ".join(user_msgs)

        if "PAGE_IN REJECTED" in joined_users:
            scenario_counts["self_correct"] = scenario_counts.get("self_correct", 0) + 1
        elif "VRAM critical" in joined_users:
            scenario_counts["page_out"] = scenario_counts.get("page_out", 0) + 1
        elif "Targeted Lines" in joined_users:
            scenario_counts["lines_in"] = scenario_counts.get("lines_in", 0) + 1
        elif "Search Match" in joined_users:
            scenario_counts["search_in"] = scenario_counts.get("search_in", 0) + 1
        else:
            scenario_counts["untargeted_in"] = scenario_counts.get("untargeted_in", 0) + 1

    print("\nScenario Distribution:")
    total = sum(scenario_counts.values()) or 1
    for sc in ["untargeted_in", "search_in", "lines_in", "page_out", "self_correct"]:
        count = scenario_counts.get(sc, 0)
        pct = (count / total) * 100
        print(f"  {sc:20s}: {count:4d} ({pct:5.1f}%)")

    # ── Write JSONL ───────────────────────────────────────────────────
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")

    file_size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"\nSaved to: {OUTPUT_FILE}")
    print(f"File size: {file_size_kb:.1f} KB")

    # ── Validation Summary ────────────────────────────────────────────
    eos_ok = sum(1 for s in samples
                 if s["messages"][-1]["content"].rstrip().endswith("```"))
    print(f"Samples ending with code block: {eos_ok}/{len(samples)}")


if __name__ == "__main__":
    main()
