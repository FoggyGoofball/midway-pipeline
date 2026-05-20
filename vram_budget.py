"""
vram_budget.py — VRAM Budget Tracker (Advisory Only)
=====================================================
Tracks cumulative model consumption across the 12GB unified memory budget
for telemetry and diagnostics. NEVER blocks model loads — that is handled
by Ollama's keep_alive=0 eviction (one model at a time).

All values carefully tuned for 12GB unified memory (Steam Deck).
Context windows are now capped per-model in working_commit_ollama.py
at 8192 for all heavy models; phi3.5 (3.8B) is the only exception at 16384.

Calculation basis (q4_K_M weights + q8_0 KV cache at 8192 ctx):
   1B model:  ~1-2GB at 8192 ctx   → budget cost: 1.5
   7B model:  ~5.5GB at 8192 ctx   → budget cost: 5.5   (was 7.4 at 32K)
   8B model:  ~6.0GB at 8192 ctx   → budget cost: 6.0   (was ~7.5 at 32K)
   9B model:  ~6.5GB at 8192 ctx   → budget cost: 6.5
  14B model:  ~9.0GB at 8192 ctx   → budget cost: 9.0   (was 10.2 at 16K)
  phi3.5 3.8B:~2.5GB at 16384 ctx  → budget cost: 2.5
"""

from __future__ import annotations

from typing import Dict, Optional, Set

# ── VRAM Budget Configuration ──────────────────────────────────────────────
# 12GB total VRAM, with 1.5GB headroom for OS/Ollama overhead
VRAM_TOTAL_GB: float = 12.0
VRAM_HEADROOM_GB: float = 1.5
VRAM_SAFE_BUDGET: float = VRAM_TOTAL_GB - VRAM_HEADROOM_GB  # 10.5 GB

# Model VRAM cost table (GB at specified context windows)
# Mapping: model tag → (base_cost_GB_at_8K, cost_per_K_of_context)
# Context beyond 8K adds ~0.12 GB per 1K tokens (q8_0 KV cache)
_MODEL_COST_TABLE: Dict[str, tuple] = {
    "qwen2.5-coder:1.5b": (1.5, 0.05),
    "qwen2.5-coder:7b":   (5.5, 0.10),   # 5.5 GB at 8K (was 5.0 — more accurate at 8K)
    "qwen3.5:9b":         (6.5, 0.12),
    "phi3:14b":           (9.0, 0.15),   # 9.0 GB at 8K (was 10.5 at 16K)
    "phi3.5":             (2.5, 0.08),   # 3.8B mini — pre-summarizer only (16K ctx)
    "llama3.1:8b":        (6.0, 0.12),
    "llama3.2:1b":        (1.5, 0.05),
    "qwen3.5:14b":        (9.0, 0.12),
    # Fallback catch-all for unknown models
    "_default":           (4.0, 0.10),
}

# ── Active Model Registry ─────────────────────────────────────────────────
# Tracks currently loaded models and their context windows
_active_registry: Dict[str, float] = {}  # model_name → estimated_GB
_total_used_gb: float = 0.0


def _estimate_model_gb(model_name: str, ctx_size: int) -> float:
    """Estimate VRAM footprint of a model at the given context window."""
    model_lower = model_name.lower()
    base, per_k = _MODEL_COST_TABLE.get("_default")
    for key, (b, pk) in _MODEL_COST_TABLE.items():
        if key in model_lower:
            base, per_k = b, pk
            break
    # Extra KV cache for context beyond 8K
    extra_ctx = max(0, ctx_size - 8192)
    extra_gb = (extra_ctx / 1024) * per_k
    return base + extra_gb


def register_model(model_name: str, ctx_size: int = 8192) -> bool:
    """Register a model as loaded in VRAM. Always returns True (advisory only).

    Logs VRAM budget warnings if the model would exceed budget, but NEVER
    blocks the load. The actual VRAM management is handled by Ollama's
    keep_alive=0 eviction — one model at a time.

    If the model is already registered (hot-loaded), returns True without
    incrementing budget.
    """
    global _total_used_gb

    # Already loaded — no change
    if model_name in _active_registry:
        return True

    cost = _estimate_model_gb(model_name, ctx_size)
    if _total_used_gb + cost > VRAM_SAFE_BUDGET:
        print(
            f"  [VRAM Budget] ⚠ Warning: '{model_name}' "
            f"({cost:.1f} GB estimated) may exceed budget. "
            f"Used: {_total_used_gb:.1f}/{VRAM_SAFE_BUDGET:.1f} GB. "
            f"(Advisory only — load proceeding.)"
        )

    _active_registry[model_name] = cost
    _total_used_gb += cost
    print(
        f"  [VRAM Budget] 📊 Loaded '{model_name}' ({cost:.1f} GB). "
        f"Used: {_total_used_gb:.1f}/{VRAM_SAFE_BUDGET:.1f} GB "
        f"({(_total_used_gb/VRAM_SAFE_BUDGET)*100:.0f}%)"
    )
    return True


def unregister_model(model_name: str) -> None:
    """Remove a model from the VRAM budget when it's evicted."""
    global _total_used_gb

    if model_name in _active_registry:
        cost = _active_registry.pop(model_name)
        _total_used_gb -= cost
        print(
            f"  [VRAM Budget] 📊 Evicted '{model_name}' ({cost:.1f} GB). "
            f"Used: {_total_used_gb:.1f}/{VRAM_SAFE_BUDGET:.1f} GB"
        )


def can_load_model(model_name: str, ctx_size: int = 8192) -> bool:
    """Check if loading a model would stay within the VRAM budget."""
    if model_name in _active_registry:
        return True  # Already loaded
    cost = _estimate_model_gb(model_name, ctx_size)
    return (_total_used_gb + cost) <= VRAM_SAFE_BUDGET


def get_vram_report() -> str:
    """Return a formatted report of current VRAM usage."""
    lines = [
        f"  VRAM Budget: {_total_used_gb:.1f} / {VRAM_SAFE_BUDGET:.1f} GB used",
        f"  Pressure:    {(_total_used_gb/VRAM_SAFE_BUDGET)*100:.0f}% of budget",
        f"  Models loaded:",
    ]
    if not _active_registry:
        lines.append("    (none)")
    else:
        for model, cost in sorted(_active_registry.items()):
            lines.append(f"    - {model}: {cost:.1f} GB")
    return "\n".join(lines)


def get_models_to_evict(model_name: str, ctx_size: int = 8192) -> Set[str]:
    """Return set of currently loaded models that should be evicted to make
    room for a new model. Returns empty set if there's enough room already.
    (Unused by main eviction code — kept for telemetry/safety net.)"""
    if can_load_model(model_name, ctx_size):
        return set()

    cost = _estimate_model_gb(model_name, ctx_size)
    needed = (_total_used_gb + cost) - VRAM_SAFE_BUDGET

    # Evict smallest models first (least costly to reload)
    to_evict: Set[str] = set()
    freed = 0.0
    for m, c in sorted(_active_registry.items(), key=lambda x: x[1]):
        if freed >= needed:
            break
        to_evict.add(m)
        freed += c

    return to_evict


def reset_budget() -> None:
    """Clear the budget registry (for testing or pipeline reset)."""
    global _active_registry, _total_used_gb
    _active_registry.clear()
    _total_used_gb = 0.0
