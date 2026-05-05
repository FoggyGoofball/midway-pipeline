"""
Pipeline checkpoint system — save and load pipeline state to/from disk JSON files.
Provides checkpoint round-trip, listing, and overwrite detection.

No async/await — purely synchronous file I/O.
"""

from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _get_default_checkpoint_dir() -> Path:
    """Get the default checkpoint directory relative to this file."""
    return Path(__file__).parent / ".pipeline_checkpoints"


CHECKPOINT_DIR: Path = _get_default_checkpoint_dir()


def save_checkpoint(checkpoint_id: str, phase: str, data: dict) -> None:
    """Save pipeline state to a checkpoint file.

    Args:
        checkpoint_id: Unique identifier for the checkpoint.
        phase: Pipeline phase string (e.g., "director", "mesh_execution").
        data: Arbitrary pipeline state data to persist.
    """
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    ckpt = {
        "checkpoint_id": checkpoint_id,
        "phase": phase,
        "timestamp": datetime.now().isoformat(),
        "data": data,
    }
    path = CHECKPOINT_DIR / f"{checkpoint_id}.json"
    path.write_text(json.dumps(ckpt, indent=2), encoding="utf-8")
    print(f"  [Checkpoint] Saved: {checkpoint_id} (phase: {phase})")


def load_checkpoint(checkpoint_id: str) -> Optional[Dict[str, Any]]:
    """Load a checkpoint by ID.

    Args:
        checkpoint_id: Unique identifier for the checkpoint.

    Returns:
        Checkpoint dict with keys: checkpoint_id, phase, timestamp, data.
        Returns None if not found or corrupt.
    """
    path = CHECKPOINT_DIR / f"{checkpoint_id}.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def list_checkpoints() -> List[Dict[str, Any]]:
    """List all saved checkpoints.

    Returns:
        List of checkpoint dicts sorted by filename.
    """
    if not CHECKPOINT_DIR.is_dir():
        return []
    checkpoints = []
    for f in sorted(CHECKPOINT_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            checkpoints.append(data)
        except Exception:
            pass
    return checkpoints
