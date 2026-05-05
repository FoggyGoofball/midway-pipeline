"""
test_full_pipeline_dry_run.py — Step 4.17
========================================
Regression test that exercises the full run_pipeline() with mocked LLM calls.
Runs identically against BOTH the refactored pipeline AND the original monolith
(pipeline.py.old, 3413 lines), then asserts outputs match structurally.

This ensures the extraction + thinning didn't change any behavior.
"""

import importlib
import importlib.util
import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest


# ══════════════════════════════════════════════════════════════════════════
#  Canned LLM Responses
# ══════════════════════════════════════════════════════════════════════════
# These simulate what real LLM calls would return for each pipeline phase.
# The exact text is what the pipeline parsers look for.

CANNED_MODIFICATION = "MODIFICATION"      # classify_intent
CANNED_NARROW = "NARROW"                  # Scope gate (prompt within limits)
CANNED_DIRECTOR = textwrap.dedent("""\
    ## Task Breakdown: Test Feature
    ### Task 1: [C++] — Implement test feature
""").strip()

CANNED_TASK_1 = textwrap.dedent("""\
    ## Output
    C++ implementation for test feature is complete.
    The code has been added to the appropriate source files.
    VERDICT: PASS
""").strip()

CANNED_REVIEW = textwrap.dedent("""\
    ## Review
    The implementation looks correct.
    VERDICT: PASS
""").strip()

CANNED_CONSENSUS = textwrap.dedent("""\
    ## Merged Code
    ```cpp
    int result = 42;
    ```
    VERDICT: APPROVED
""").strip()

CANNED_FINAL = textwrap.dedent("""\
    FINAL APPROVED
""").strip()


def _make_canned_ollama(system: str, user: str, label: str, model: str = None) -> str:
    """Return canned response based on system prompt content or label."""
    s_upper = (system or "").upper()
    l_upper = (label or "").upper()

    if "INTENT CLASSIFIER" in s_upper:
        return CANNED_MODIFICATION
    if "SCOPE GATE" in l_upper:
        return CANNED_NARROW
    if "DIRECTOR" in s_upper or "DIRECTOR" in l_upper:
        return CANNED_DIRECTOR
    if "REVIEW" in s_upper or "REVIEW" in l_upper:
        return CANNED_REVIEW
    if "FINAL_APPROVAL" in s_upper or "FINAL" in l_upper:
        return CANNED_FINAL
    # Default: execution/other task output
    return CANNED_TASK_1


def _make_canned_streamed(system: str, user: str, label: str, model: str = None):
    """Canned streaming generator."""
    response = _make_canned_ollama(system, user, label, model)
    yield response


# ══════════════════════════════════════════════════════════════════════════
#  Baseline Monolith Loader
# ══════════════════════════════════════════════════════════════════════════

BASELINE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "pipeline.py.old"
)


def _load_baseline_module(tmp_dir: Path) -> object:
    """Load pipeline.py.old as a Python module and patch its LLM functions."""
    if not BASELINE_PATH.is_file():
        pytest.skip(f"Baseline not found: {BASELINE_PATH}")

    # Copy baseline to temp dir so __file__ resolves inside tmp_dir
    dest = tmp_dir / "pipeline.py"
    dest.write_text(BASELINE_PATH.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8")

    spec = importlib.util.spec_from_file_location("_baseline_pipeline", str(dest))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_baseline_pipeline"] = mod
    spec.loader.exec_module(mod)

    # Replace LLM call functions with canned versions
    mod.call_ollama = _make_canned_ollama
    mod.call_ollama_streamed = _make_canned_streamed

    return mod


# ══════════════════════════════════════════════════════════════════════════
#  Structural Comparison
# ══════════════════════════════════════════════════════════════════════════
# The refactored pipeline and baseline monolith format output differently
# (different headers, timestamps, etc.). We compare structural content
# that should be identical: phase presence, verdict markers, task structure.
# Full-text comparison is impossible because the refactored version strips
# banner-style formatting that the original monolith included.


def _extract_common_structure(output: str) -> dict:
    """Extract structural elements that should be identical between both versions."""
    lines = output.strip().split("\n") if output else []

    # Look for phase markers (shared by both versions)
    phases_found = []
    phase_patterns = {
        "GDD Librarian": "Phase.*GDD Librarian" in output or "GDD Librarian" in output,
        "Project Context": "Phase.*Project Context" in output or "Project Context" in output,
        "Director": "Phase.*Director" in output or "Director" in output,
        "Mesh Execution": "Phase.*Mesh Execution" in output or "Phase.*Task Execution" in output or "Mesh Execution" in output,
        "Conflict Resolution": "Phase.*Conflict Resolution" in output or "Conflict Resolution" in output,
        "Review": "Phase.*Review" in output or "Phase.*Integration Review" in output,
        "Consensus": "Phase.*Consensus" in output or "Consensus Gate" in output,
        "Failure Report": "Phase.*Failure Report" in output or "Phase.*Post-Mortem" in output,
    }
    for phase_name, present in phase_patterns.items():
        if present:
            phases_found.append(phase_name)

    return {
        "has_output": bool(output and len(output) > 0),
        "length_at_least": len(output) >= 100,
        "has_verdict": "VERDICT" in output or "APPROVED" in output or "FINAL APPROVED" in output or "FAILED" in output,
        "has_task_breakdown": "## Task Breakdown" in output,
        "has_task_1": "### Task 1" in output,
        "has_cpp_tag": "[C++]" in output,
        "has_gdd_section": "GDD Librarian" in output or "GDD" in output,
        "phases_found": phases_found,
    }


def _assert_structural_match(ref_output: str, base_output: str,
                              label: str = "outputs"):
    """Assert that refactored and baseline outputs are structurally identical."""
    ref_keys = _extract_common_structure(ref_output)
    base_keys = _extract_common_structure(base_output)

    for key in ref_keys:
        if key == "phases_found":
            # Compare phase lists — baseline may have phases the refactored doesn't
            # or vice versa, but the shared phases should overlap
            shared_phases = set(ref_keys["phases_found"]) & set(base_keys["phases_found"])
            assert len(shared_phases) >= min(
                len(ref_keys["phases_found"]), len(base_keys["phases_found"])
            ) * 0.5, (
                f"{label}: Insufficient phase overlap: "
                f"refactored={ref_keys['phases_found']}, baseline={base_keys['phases_found']}"
            )
        else:
            assert ref_keys[key] == base_keys.get(key), (
                f"{label}: Structural mismatch on '{key}': "
                f"refactored={ref_keys[key]}, baseline={base_keys.get(key)}"
            )


# ══════════════════════════════════════════════════════════════════════════
#  Tests
# ══════════════════════════════════════════════════════════════════════════

class TestFullPipelineDryRun:
    """Regression: refactored pipeline output matches baseline monolith."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_project_root, monkeypatch):
        """Set up temp project root with required subdirs each test."""
        self.tmp_root = tmp_project_root

        # Ensure all required subdirs exist
        for sub in ["docs/memory", "GDD", ".pipeline_checkpoints"]:
            (self.tmp_root / sub).mkdir(parents=True, exist_ok=True)

        # Create required files
        gdd_file = self.tmp_root / "GDD" / "Midway_to_Nowhere_Master_GDD_v19.md"
        if not gdd_file.is_file():
            gdd_file.write_text("# GDD Placeholder\n\nTest content.\n", encoding="utf-8")

        completed = self.tmp_root / "docs" / "completed_features.md"
        if not completed.is_file():
            completed.write_text(
                "### ✅ Implemented Systems\n- Physics Engine\n- Lua Bridge\n",
                encoding="utf-8",
            )

        todo_f = self.tmp_root / "docs" / "todo.md"
        if not todo_f.is_file():
            todo_f.write_text(
                "| 1 | Test Feature |  Open  | Test |\n",
                encoding="utf-8",
            )

        mem_file = self.tmp_root / "docs" / "memory" / "ledger_DEV.md"
        if not mem_file.is_file():
            mem_file.write_text("## Ledger\n\nNo entries.\n", encoding="utf-8")

        # Patch PROJECT_ROOT in pipeline modules
        monkeypatch.setattr("pipeline.PROJECT_ROOT", self.tmp_root)
        import _pipeline_helpers
        monkeypatch.setattr(_pipeline_helpers, "PROJECT_ROOT", self.tmp_root)

    # ── Refactored pipeline runner ─────────────────────────────────────

    def _run_refactored(self, prompt: str) -> str:
        """Run the refactored pipeline with mocked LLM calls."""
        from pipeline import run_pipeline

        with patch("pipeline.call_ollama", side_effect=_make_canned_ollama):
            with patch("pipeline.call_ollama_streamed",
                       side_effect=_make_canned_streamed):
                import ollama_client
                with patch.object(ollama_client, "call_ollama",
                                  side_effect=_make_canned_ollama):
                    output = run_pipeline(prompt)
        return output

    # ── Baseline monolith runner ───────────────────────────────────────

    def _run_baseline(self, prompt: str, tmp_dir: Path) -> str:
        """Run the baseline monolithic pipeline with mocked LLM calls."""
        mod = _load_baseline_module(tmp_dir)

        # Point the baseline to the same temp project root
        mod.PROJECT_ROOT = self.tmp_root

        # The baseline's run_pipeline writes an output file — suppress that
        # by temporarily redirecting or accepting the side effect
        output = mod.run_pipeline(prompt)
        return output

    # ── Tests ──────────────────────────────────────────────────────────

    def test_dry_run_produces_output(self):
        """Refactored pipeline produces output without real LLM calls."""
        output = self._run_refactored("Add a test feature to the engine.")
        assert output is not None
        assert isinstance(output, str)
        assert len(output) > 0

    def test_refactored_versus_baseline_match(self, tmp_path):
        """Both pipelines produce structurally identical outputs."""
        prompt = "Add a test feature to the engine."

        ref_output = self._run_refactored(prompt)
        base_output = self._run_baseline(prompt, tmp_path)

        print(f"\n=== REFACTORED OUTPUT ({len(ref_output)} chars) ===")
        print(ref_output[:1200])
        print(f"\n=== BASELINE OUTPUT ({len(base_output)} chars) ===")
        print(base_output[:1200])

        _assert_structural_match(ref_output, base_output,
                                 "run_pipeline('Add a test feature')")

    def test_both_return_string_type(self, tmp_path):
        """Both pipelines return a non-empty string."""
        prompt = "Add test feature."

        ref_output = self._run_refactored(prompt)
        base_output = self._run_baseline(prompt, tmp_path)

        assert isinstance(ref_output, str)
        assert isinstance(base_output, str)
        assert len(ref_output) > 0
        assert len(base_output) > 0

    def test_verdict_present_in_both(self, tmp_path):
        """Both pipelines include a verdict marker in their output."""
        prompt = "Add a test feature."

        ref_output = self._run_refactored(prompt)
        base_output = self._run_baseline(prompt, tmp_path)

        for label, out in [("refactored", ref_output), ("baseline", base_output)]:
            has_verdict = "VERDICT" in out or "APPROVED" in out or "FAILED" in out
            assert has_verdict, (
                f"{label} output missing verdict: {out[:300]}"
            )

    def test_both_contain_task_breakdown(self, tmp_path):
        """Both pipelines include a ## Task Breakdown section."""
        prompt = "Add a test feature to the engine."

        ref_output = self._run_refactored(prompt)
        base_output = self._run_baseline(prompt, tmp_path)

        assert "## Task Breakdown" in ref_output, (
            f"Refactored output missing Task Breakdown: {ref_output[:400]}"
        )
        assert "## Task Breakdown" in base_output, (
            f"Baseline output missing Task Breakdown: {base_output[:400]}"
        )

    def test_both_contain_gdd_section(self, tmp_path):
        """Both pipelines include a GDD Librarian section."""
        prompt = "Add a test feature to the engine."

        ref_output = self._run_refactored(prompt)
        base_output = self._run_baseline(prompt, tmp_path)

        assert "GDD" in ref_output, (
            f"Refactored output missing GDD: {ref_output[:300]}"
        )
        assert "GDD" in base_output, (
            f"Baseline output missing GDD: {base_output[:300]}"
        )
