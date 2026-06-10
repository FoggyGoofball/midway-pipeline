#!/usr/bin/env bash
# =========================================================================
# setup_lora_server.sh
# =========================================================================
# One-time setup script to run on the Steam Deck (inference server).
#
# This script:
#   1. Installs Python dependencies (Unsloth, PyTorch, etc.)
#   2. Creates a working directory for the LoRA trainer
#   3. Provides instructions to start the server
#
# Prerequisites:
#   - Steam Deck with internet access
#   - Python 3.10+ installed (pre-installed on SteamOS)
#   - Ollama already running for inference
#
# Usage:
#   # Copy the lora_generator/ folder to the Steam Deck first:
#   scp -r ./lora_generator/ deck@192.168.0.16:~/midway/lora_generator/
#
#   # Then SSH into the Steam Deck and run:
#   bash ~/midway/lora_generator/setup_lora_server.sh
# =========================================================================

set -e  # Exit on any error

echo "============================================="
echo "  LoRA Training Server — Steam Deck Setup"
echo "============================================="
echo ""

# -- Configuration ---------------------------------------------------------
LORA_DIR="$HOME/midway/lora_generator"
echo "[1/4] Working directory: $LORA_DIR"

# Create directory if it doesn't exist
mkdir -p "$LORA_DIR"

# -- Step 2: Install Python dependencies -----------------------------------
echo ""
echo "[2/4] Installing Python dependencies..."
echo ""

# Upgrade pip
python3 -m pip install --upgrade pip --quiet

# Install core ML dependencies
# Unsloth auto-detects CUDA vs ROCm — on Steam Deck it's ROCm (AMD GPU)
python3 -m pip install --upgrade \
    unsloth \
    torch \
    transformers \
    datasets \
    accelerate \
    peft \
    trl \
    bitsandbytes \
    --quiet

echo ""
echo "  ✓ Core dependencies installed."

# Verify torch detected CUDA/ROCm
python3 -c "
import torch
if torch.cuda.is_available():
    print(f'  ✓ CUDA/ROCm available: {torch.cuda.get_device_name(0)}')
    print(f'  ✓ VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB')
else:
    print('  ⚠ CUDA/ROCm NOT detected — training will be extremely slow!')
    print('  Make sure PyTorch is installed with ROCm support for Steam Deck.')
    print('  Try: pip install torch --index-url https://download.pytorch.org/whl/rocm6.0')
"

# -- Step 3: Check dataset -------------------------------------------------
echo ""
echo "[3/4] Checking dataset..."
echo ""

DATASET="$LORA_DIR/paging_lora_dataset.jsonl"
if [ -f "$DATASET" ]; then
    LINES=$(wc -l < "$DATASET")
    SIZE=$(du -h "$DATASET" | cut -f1)
    echo "  ✓ Dataset found: $DATASET"
    echo "    Lines: $LINES"
    echo "    Size:  $SIZE"
else
    echo "  ⚠ Dataset not found at $DATASET"
    echo "  The server will generate it on-demand, or copy it from your laptop:"
    echo "    scp ./lora_generator/paging_lora_dataset.jsonl deck@192.168.0.16:$DATASET"
fi

# -- Step 4: Instructions --------------------------------------------------
echo ""
echo "[4/4] Setup complete!"
echo ""
echo "============================================="
echo "  To start the LoRA training server:"
echo "============================================="
echo ""
echo "  nohup python3 $LORA_DIR/lora_trainer_server.py \\"
echo "    --port 8766 \\"
echo "    > $LORA_DIR/server.log 2>&1 &"
echo ""
echo "  Check it's running:"
echo "    curl http://localhost:8766/health"
echo ""
echo "  View logs:"
echo "    tail -f $LORA_DIR/server.log"
echo ""
echo "  From your laptop, trigger training:"
echo "    curl -N http://192.168.0.16:8766/lora/train"
echo "    curl -N http://192.168.0.16:8766/lora/train?epochs=2&lr=1e-5"
echo ""
echo "  Check training status:"
echo "    curl http://192.168.0.16:8766/lora/status"
echo ""
echo "  Download trained adapter:"
echo "    curl -O http://192.168.0.16:8766/lora/download"
echo ""
echo "  To view training log from laptop:"
echo "    curl http://192.168.0.16:8766/lora/log"
echo "    curl http://192.168.0.16:8766/lora/log?lines=20"
echo ""
echo "============================================="
echo "  After training completes, create an"
echo "  Ollama model with the LoRA adapter:"
echo "============================================="
echo ""
echo "  cd $LORA_DIR"
echo "  ollama create qwen-paging -f - << 'EOF'"
echo "  FROM qwen2.5-coder:7b"
echo "  ADAPTER ./lora_output/adapter_model.safetensors"
echo "  TEMPLATE \"\"\"{{ .Prompt }}\"\"\""
echo "  EOF"
echo ""
echo "  Then update ollama_config.py on your laptop:"
echo '    CODER_MODEL = "qwen-paging"'
echo ""
echo "============================================="
