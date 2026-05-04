#!/bin/bash
# ───────────────────────────────────────────────────────────
#  Midway Pipeline — Ollama Model Pull Script (Steam Deck)
# ───────────────────────────────────────────────────────────
# Run this script on the Steam Deck to pull all models
# required by the Midway mesh pipeline.
#
# Usage:
#   chmod +x pull_models.sh
#   ./pull_models.sh
#
# Models (5 total, ~15 GB download):
#   1. phi-3:14b-q4_K_M    [Reviewer / Reasoning Gate — 14B, ~8GB VRAM]
#   2. qwen2.5-coder:7b     [Coder / Execution — 7B, ~4.5GB VRAM]
#   3. llama3.1:8b-instruct-q4_K_M  [Librarian / Fallback — 8B, ~4.7GB]
#   4. qwen2.5-coder-1.5b   [Syntax Gate — 1.5B micro-model]
#   5. llama-3.2-1b         [Intent Classifier — 1B micro-model]
# ───────────────────────────────────────────────────────────

# Exit on first failure
set -e

echo "═══════════════════════════════════════════════════"
echo "  Midway Pipeline — Pulling Ollama Models"
echo "═══════════════════════════════════════════════════"
echo ""

# Verify Ollama is installed
if ! command -v ollama &> /dev/null; then
    echo "❌ ERROR: Ollama is not installed."
    echo "   Install it first: curl -fsSL https://ollama.com/install.sh | sh"
    exit 1
fi

echo "✅ Ollama found"
echo ""

# Track success/failure
SUCCESS=0
FAILED=0

pull_model() {
    local MODEL="$1"
    local LABEL="$2"
    echo "────────────────────────────────────────────────"
    echo "📥 Pulling: $LABEL"
    echo "   Model: $MODEL"
    echo "────────────────────────────────────────────────"
    if ollama pull "$MODEL" 2>&1; then
        echo "✅  $LABEL — pulled successfully"
        SUCCESS=$((SUCCESS + 1))
    else
        echo "❌  $LABEL — FAILED"
        FAILED=$((FAILED + 1))
    fi
    echo ""
}

# ── Primary models ────────────────────────────────────────
pull_model "phi-3:14b-q4_K_M"     "Reviewer / Reasoning Gate (14B)"
pull_model "qwen2.5-coder:7b"     "Coder / Execution (7B)"
pull_model "llama3.1:8b-instruct-q4_K_M" "Librarian / Fallback Reviewer (8B)"

# ── Micro-models ──────────────────────────────────────────
pull_model "qwen2.5-coder-1.5b"   "Syntax Gate (1.5B micro)"
pull_model "llama-3.2-1b"         "Intent Classifier (1B micro)"

# ── Summary ───────────────────────────────────────────────
echo "═══════════════════════════════════════════════════"
echo "  Pull Complete"
echo "═══════════════════════════════════════════════════"
echo "  ✅ Succeeded: $SUCCESS"
echo "  ❌ Failed:    $FAILED"
echo ""
if [ "$SUCCESS" -gt 0 ] && [ "$FAILED" -eq 0 ]; then
    echo "  All models pulled successfully! 🎉"
    echo ""
    echo "  Run the pipeline:"
    echo "    python pipeline.py \"your feature request\""
elif [ "$FAILED" -gt 0 ]; then
    echo "  ⚠️  Some models failed. Check errors above and retry."
    exit 1
fi
echo "═══════════════════════════════════════════════════"
