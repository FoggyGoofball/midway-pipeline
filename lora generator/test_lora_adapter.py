#!/usr/bin/env python3
"""
test_lora_adapter.py
====================
Post-training validation suite for the LoRA adapter.

Tests cover all 5 protocol scenarios with realistic prompts and validates
that the model emits correct XML tokens instead of hallucinating content.

Prerequisites
-------------
  pip install unsloth transformers torch

Usage
-----
  # Test with the trained adapter merged
  python lora_generator/test_lora_adapter.py --adapter lora_generator/lora_output

  # Test against the base model (no adapter) for baseline comparison
  python lora_generator/test_lora_adapter.py --base

  # Test against a running Ollama model
  python lora_generator/test_lora_adapter.py --ollama qwen2.5-coder:7b
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

# ── Expected XML Patterns ───────────────────────────────────────────────
PAGE_IN_RE = re.compile(
    r'<invoke_kernel>\s*<action>PAGE_IN</action>\s*<target>(.*?)</target>'
    r'(?:\s*<lines>(.*?)</lines>)?'
    r'(?:\s*<search>(.*?)</search>)?'
    r'\s*</invoke_kernel>',
    re.DOTALL,
)

PAGE_OUT_RE = re.compile(
    r'<invoke_kernel>\s*<action>PAGE_OUT</action>\s*<target>(.*?)</target>'
    r'\s*</invoke_kernel>',
    re.DOTALL,
)

PAGE_IN_REJECTED_RE = re.compile(
    r'\[SYSTEM KERNEL: PAGE_IN REJECTED',
)

# ── Protocol Prompt (injected into system messages) ─────────────────────
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


# ── Test Cases ──────────────────────────────────────────────────────────

TEST_CASES: List[Dict] = [
    # Test 1: Untargeted PAGE_IN on small file
    {
        "name": "untargeted_page_in_small_file",
        "system": (
            f"You are a Python developer.\n{PROTOCOL_TEXT}\n\n"
            f"<VRAM_STUB id=\"src/utils/helpers.py\" "
            f"summary=\"Contains parse_config...\" />"
        ),
        "user": "Implement the parse_config feature. Review the reference and proceed.",
        "expect": {
            "type": "PAGE_IN",
            "has_target": True,
            "has_search": False,
            "has_lines": False,
        },
        "description": "Model should fetch full small file via untargeted PAGE_IN",
    },
    # Test 2: Search-targeted PAGE_IN on large file
    {
        "name": "search_targeted_page_in_large_file",
        "system": (
            f"You are a Rust developer.\n{PROTOCOL_TEXT}\n\n"
            f"<VRAM_STUB id=\"src/core/engine.rs\" "
            f"summary=\"Contains trait EventHandler...\" />"
        ),
        "user": "Fix the EventHandler implementation. The file is large, search for the trait definition.",
        "expect": {
            "type": "PAGE_IN",
            "has_target": True,
            "has_search": True,
        },
        "description": "Model should use <search> tag for targeted page-in of large file",
    },
    # Test 3: Lines-targeted PAGE_IN on large file
    {
        "name": "lines_targeted_page_in_large_file",
        "system": (
            f"You are a Go developer.\n{PROTOCOL_TEXT}\n\n"
            f"<VRAM_STUB id=\"src/core/engine.go\" "
            f"summary=\"Contains type HandlerFunc...\" />"
        ),
        "user": "Update lines 25-60 in engine.go to fix the ServeHTTP routing. Fetch those lines exactly.",
        "expect": {
            "type": "PAGE_IN",
            "has_target": True,
            "has_lines": True,
        },
        "description": "Model should use <lines> tag for targeted page-in of specific range",
    },
    # Test 4: PAGE_OUT on VRAM critical
    {
        "name": "page_out_vram_critical",
        "system": (
            f"You are a Java developer.\n{PROTOCOL_TEXT}"
        ),
        "user": (
            "Continue the implementation. "
            "[SYSTEM KERNEL: VRAM critical — 85% of context window consumed. "
            "Emit PAGE_OUT to free memory before proceeding.]"
        ),
        "expect": {
            "type": "PAGE_OUT",
            "has_target": True,
        },
        "description": "Model should emit PAGE_OUT when VRAM critical warning is present",
    },
    # Test 5: Self-correction after hard cap rejection
    {
        "name": "self_correct_after_rejection",
        "system": (
            f"You are a TypeScript developer.\n{PROTOCOL_TEXT}\n\n"
            f"<VRAM_STUB id=\"src/services/pipeline.ts\" "
            f"summary=\"Contains Observable...\" />"
        ),
        "user": (
            "Implement the pipe operator. "
            "[SYSTEM KERNEL: PAGE_IN REJECTED — 'src/services/pipeline.ts' exceeds the "
            "untargeted Hard Cap. RETRY with a targeting tag.]"
        ),
        "expect": {
            "type": "PAGE_IN",
            "has_target": True,
            "has_search": True,  # Should retry with search
        },
        "description": "Model should retry with targeting tag after kernel rejection",
    },
    # Test 6: No trailing filler after </invoke_kernel>
    {
        "name": "no_trailing_filler_after_eos",
        "system": (
            f"You are a C# developer.\n{PROTOCOL_TEXT}\n\n"
            f"<VRAM_STUB id=\"src/api/handlers.cs\" "
            f"summary=\"Contains IActionResult...\" />"
        ),
        "user": "Show me the IActionResult interface from the handlers file. Use a targeted search.",
        "expect": {
            "type": "PAGE_IN",
            "has_target": True,
            "eos_check": True,  # Must end with </invoke_kernel>, no trailing text
        },
        "description": "Model must hard-stop at </invoke_kernel> without trailing filler",
    },
    # Test 7: PAGE_IN with small file (untargeted, full file)
    {
        "name": "untargeted_page_in_markdown",
        "system": (
            f"You are a Ruby developer.\n{PROTOCOL_TEXT}\n\n"
            f"<VRAM_STUB id=\"docs/architecture.md\" "
            f"summary=\"Contains system design...\" />"
        ),
        "user": "Review the system design section and update the data flow.",
        "expect": {
            "type": "PAGE_IN",
            "has_target": True,
            "has_search": False,
            "has_lines": False,
        },
        "description": "Model should fetch full small markdown file via untargeted PAGE_IN",
    },
    # Test 8: VRAM critical with explicit PAGE_OUT
    {
        "name": "page_out_then_continue",
        "system": (
            f"You are a Kotlin developer.\n{PROTOCOL_TEXT}"
        ),
        "user": (
            "I need to add a Room entity. "
            "[SYSTEM KERNEL: VRAM critical — 90% of context window consumed. "
            "You must free memory via PAGE_OUT before continuing.]"
        ),
        "expect": {
            "type": "PAGE_OUT",
            "has_target": True,
        },
        "description": "Model should recognize VRAM critical and emit PAGE_OUT to continue",
    },
]


# ── Model Inference ─────────────────────────────────────────────────────

def _build_prompt(system: str, user: str) -> str:
    """Build a ChatML prompt string."""
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )


class BaseModelWrapper:
    """Wrapper for HuggingFace / Unsloth model inference."""

    def __init__(self, adapter_path: Optional[str] = None):
        from unsloth import FastLanguageModel
        import torch

        model_name = "unsloth/Qwen2.5-Coder-7B"

        print(f"Loading base model: {model_name}")
        if adapter_path:
            print(f"  With LoRA adapter: {adapter_path}")

        self.model, self.tokenizer = FastLanguageModel.from_pretrained(
            model_name=model_name,
            max_seq_length=4096,
            dtype=torch.float16,
            load_in_4bit=True,
            device_map="auto",
        )

        if adapter_path:
            from peft import PeftModel
            print(f"  Loading adapter weights from {adapter_path}...")
            self.model = PeftModel.from_pretrained(self.model, adapter_path)

        # Enable faster inference
        FastLanguageModel.for_inference(self.model)
        print("  Model ready for inference.")

    def generate(self, system: str, user: str, max_tokens: int = 512) -> str:
        prompt = _build_prompt(system, user)
        inputs = self.tokenizer([prompt], return_tensors="pt").to(self.model.device)

        outputs = self.model.generate(
            **inputs,
            max_new_tokens=max_tokens,
            temperature=0.1,  # Low temp for deterministic output
            top_p=0.9,
            do_sample=True,
            pad_token_id=self.tokenizer.eos_token_id,
        )

        decoded = self.tokenizer.decode(outputs[0], skip_special_tokens=False)
        # Extract assistant response
        assistant_marker = "<|im_start|>assistant\n"
        if assistant_marker in decoded:
            response = decoded.split(assistant_marker, 1)[1]
            # Remove trailing <|im_end|> if present
            response = response.split("<|im_end|>")[0].strip()
            return response
        return decoded.strip()


class OllamaModelWrapper:
    """Wrapper for Ollama-hosted model inference."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        print(f"Using Ollama model: {model_name}")

    def generate(self, system: str, user: str, max_tokens: int = 512) -> str:
        import urllib.request
        import json as _json

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": max_tokens,
            },
        }

        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = _json.loads(resp.read().decode("utf-8"))
                return result.get("message", {}).get("content", "").strip()
        except Exception as e:
            return f"[ERROR: {e}]"


# ── Test Runner ─────────────────────────────────────────────────────────

def run_test(model_fn: Callable, test_case: Dict) -> Tuple[bool, str, str]:
    """Run a single test case and return (passed, reason, actual_output)."""
    system = test_case["system"]
    user = test_case["user"]
    expect = test_case["expect"]

    generated = model_fn(system, user)
    expected_type = expect.get("type")

    # Check page token type
    page_in_match = PAGE_IN_RE.search(generated)
    page_out_match = PAGE_OUT_RE.search(generated)

    actual_type = None
    if page_in_match:
        actual_type = "PAGE_IN"
    elif page_out_match:
        actual_type = "PAGE_OUT"

    if actual_type != expected_type:
        return (False,
                f"Expected {expected_type}, got {actual_type or 'no page token'}",
                generated)

    # Validate target presence
    if expect.get("has_target"):
        target = None
        if page_in_match:
            target = page_in_match.group(1)
        elif page_out_match:
            target = page_out_match.group(1)
        if not target or target.strip() in ("", "filename.md", "filename.cpp"):
            return (False, f"Missing or placeholder target in {expected_type}", generated)

    # Validate search tag (should be present for search_in, absent for untargeted_in)
    if expect.get("has_search") and page_in_match:
        if not page_in_match.group(3):
            return (False, "Expected <search> tag but none found", generated)
    if expect.get("has_search") is False and page_in_match:
        if page_in_match.group(3):
            return (False, "Expected no <search> tag but one was present", generated)

    # Validate lines tag
    if expect.get("has_lines") and page_in_match:
        if not page_in_match.group(2):
            return (False, "Expected <lines> tag but none found", generated)

    # EOS check: no trailing text after </invoke_kernel>
    if expect.get("eos_check") or expect.get("type") == "PAGE_OUT":
        if "<invoke_kernel>" in generated:
            last_close = generated.rfind("</invoke_kernel>")
            trailing = generated[last_close + len("</invoke_kernel>"):].strip()
            if trailing and not trailing.startswith("```") and not trailing.startswith("[SYSTEM"):
                return (False,
                        f"Found {len(trailing)} chars of trailing text after </invoke_kernel>",
                        generated)

    return (True, "PASS", generated)


def main():
    parser = argparse.ArgumentParser(
        description="Test the paging protocol LoRA adapter"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--adapter", type=str, default=None,
        help="Path to LoRA adapter directory (default: lora_generator/lora_output)"
    )
    group.add_argument(
        "--base", action="store_true",
        help="Test against base model (no adapter)"
    )
    group.add_argument(
        "--ollama", type=str, default=None,
        help="Test against a running Ollama model (e.g., qwen2.5-coder:7b)"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available test cases without running"
    )
    parser.add_argument(
        "--filter", type=str, default=None,
        help="Regex filter for test case names"
    )

    args = parser.parse_args()

    # Filter test cases
    test_cases = TEST_CASES
    if args.filter:
        pattern = re.compile(args.filter)
        test_cases = [t for t in test_cases if pattern.search(t["name"])]

    if args.list:
        print(f"Available test cases ({len(test_cases)}):")
        for tc in test_cases:
            print(f"  {tc['name']:40s} — {tc['description']}")
        sys.exit(0)

    # Initialize model
    model_fn = None
    model_desc = ""

    if args.base:
        model_wrapper = BaseModelWrapper(adapter_path=None)
        model_fn = model_wrapper.generate
        model_desc = "Base Qwen2.5-Coder-7B (no adapter)"
    elif args.ollama:
        model_wrapper = OllamaModelWrapper(args.ollama)
        model_fn = model_wrapper.generate
        model_desc = f"Ollama: {args.ollama}"
    else:
        # Default: try adapter path
        adapter_path = args.adapter or os.path.join(SCRIPT_DIR, "lora_output")
        if not os.path.exists(adapter_path):
            print(f"ERROR: Adapter not found at {adapter_path}")
            print("Run lora_fine_tune.py first, or use --base for baseline testing.")
            sys.exit(1)
        model_wrapper = BaseModelWrapper(adapter_path=adapter_path)
        model_fn = model_wrapper.generate
        adapter_rel = os.path.relpath(adapter_path)
        model_desc = f"Qwen2.5-Coder-7B + LoRA ({adapter_rel})"

    # Run tests
    print(f"\n{'=' * 72}")
    print(f"  Testing: {model_desc}")
    print(f"  Test cases: {len(test_cases)}")
    print(f"{'=' * 72}\n")

    passed = 0
    failed = 0
    total_time = 0.0

    for i, tc in enumerate(test_cases, 1):
        print(f"[{i}/{len(test_cases)}] {tc['name']}")
        print(f"  Description: {tc['description']}")

        start = time.time()
        success, reason, output = run_test(model_fn, tc)
        elapsed = time.time() - start
        total_time += elapsed

        status = "✓ PASS" if success else "✗ FAIL"
        print(f"  Status: {status} ({elapsed:.1f}s)")
        if not success:
            print(f"  Reason: {reason}")
            failed += 1
        else:
            passed += 1

        # Show snippet of generated output
        snippet = output[:200].replace("\n", "\\n")
        print(f"  Output: \"{snippet}...\"")
        print()

    # Summary
    print(f"{'=' * 72}")
    print(f"  Results: {passed}/{len(test_cases)} passed, "
          f"{failed}/{len(test_cases)} failed")
    print(f"  Total time: {total_time:.1f}s "
          f"({total_time / max(len(test_cases), 1):.1f}s avg)")
    print(f"{'=' * 72}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
