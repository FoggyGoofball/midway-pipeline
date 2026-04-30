# Pipeline Snapshot Manager — User Guide

A git-style version control system for your AI pipeline runs. Every time an agent proposes a change to a file, it gets stored as a numbered revision with a SHA-256 fingerprint. You can browse history, restore any past version, lock important ones, and detect tampering.

---

## Quick Start

### 1. Run the pipeline normally

```bash
python pipeline.py "add a jackpot feature to the plinko attraction"
```

The pipeline automatically creates a snapshot run. Each agent output gets saved as a new revision.

### 2. See what happened

```bash
# List all snapshot runs
python pipeline_snapshot.py list

# See details for a specific run
python pipeline_snapshot.py show crumblingfacade_jackpot_20260429

# See per-file revision history
python pipeline_snapshot.py log crumblingfacade_jackpot_20260429

# See full history for one file
python pipeline_snapshot.py log crumblingfacade_jackpot_20260429 src/Engine.cpp
```

### 3. Browse and restore versions

```bash
# Restore a specific version of a file to your working tree
python pipeline_snapshot.py checkout crumblingfacade_jackpot_20260429 src/Engine.cpp v1

# See what changed between original and current
python pipeline_snapshot.py diff crumblingfacade_jackpot_20260429
```

### 4. Lock important versions

```bash
# Seal a version so it can never be evicted
python pipeline_snapshot.py seal crumblingfacade_jackpot_20260429 src/Engine.cpp v2
```

### 5. Apply or revert

```bash
# Apply all active proposals to the working tree
python pipeline_snapshot.py advance crumblingfacade_jackpot_20260429

# Revert everything back to originals
python pipeline_snapshot.py revert crumblingfacade_jackpot_20260429
```

### 6. Check integrity

```bash
# Verify all files in all runs
python pipeline_snapshot.py verify

# Verify a specific run
python pipeline_snapshot.py verify crumblingfacade_jackpot_20260429
```

---

## How Revisions Work

### The 4-Revision Limit

Each file keeps at most **4 revisions** at any time. When a 5th is added, the oldest unsealed revision is evicted (file deleted from disk).

**Exception:** `v0` (the original pre-pipeline capture) is **never evicted**. You can always revert back to the original.

### Revision Numbers

Revisions are numbered sequentially: `v0`, `v1`, `v2`, `v3`, `v4`, etc. The number is permanent — if `v1` gets evicted, that number is gone forever and won't be reused.

### Active Version

The "active" version is the one that will be written to your project files when you run `advance`. It's automatically set to the latest revision when a new one is added, but you can change it with `checkout`.

---

## CLI Commands Reference

### `list`
List all snapshot runs with summary stats.

```
python pipeline_snapshot.py list
```

### `show <run_id>`
Show detailed summary of a run.

```
python pipeline_snapshot.py show crumblingfacade_jackpot
```

### `log [run_id] [file]`
Show revision history.

| Args | Output |
|------|--------|
| _(none)_ | Same as `list` |
| `<run_id>` | Per-file summary with revision counts |
| `<run_id> <file>` | Full revision history for one file |

Example output:
```
  File: src/Engine.cpp
  Active: v3
  ──────────────────────────────────────────────────────────────────────
  → v3 🔒  2026-04-29T17:30:00  Integration Review: fixed null pointer
             by agent:Review  sha256:a1b2c3d4e5f6
    v2      2026-04-29T17:20:00  C++ Core: added jackpot multiplier
             by agent:C++ Core  sha256:b2c3d4e5f6a1
    v1      2026-04-29T17:10:00  C++ Core: proposed change (task 1)
             by agent:C++ Core  sha256:c3d4e5f6a1b2
    v0      2026-04-29T17:00:00  Original pre-pipeline capture
             by pipeline_snapshot  sha256:d4e5f6a1b2c3
```

### `checkout <run_id> <file> <version>`
Restore a specific version to the working tree.

```
python pipeline_snapshot.py checkout crumblingfacade_jackpot src/Engine.cpp v1
```

- Writes the selected version to the actual project file
- Updates the "active version" for that file
- Verifies SHA-256 before writing — refuses if tampered
- The restored file is now live in your project

### `seal <run_id> <file> <version>`
Lock a version so it can never be evicted.

```
python pipeline_snapshot.py seal crumblingfacade_jackpot src/Engine.cpp v2
```

- Sealed versions are excluded from rotation
- If all 4 slots are sealed, no new revisions can be added (rotation has nothing to evict)
- Sealed versions that are tampered with generate a `SEALED TAMPERED` warning on verify

### `verify [run_id]`
Check SHA-256 integrity of all stored files.

```
python pipeline_snapshot.py verify
python pipeline_snapshot.py verify crumblingfacade_jackpot
```

- Compares every stored file's SHA-256 against its manifest entry
- Reports missing files, hash mismatches, and tampered sealed versions
- `apply_proposals()` and `revert_all()` refuse to run if integrity check fails

### `diff <run_id> [file_filter]`
Show unified diff between original (v0) and active revision.

```
python pipeline_snapshot.py diff crumblingfacade_jackpot
python pipeline_snapshot.py diff crumblingfacade_jackpot Engine
```

### `advance <run_id>`
Apply all active proposals to the working tree.

```
python pipeline_snapshot.py advance crumblingfacade_jackpot
```

- Writes the active revision of each file to the project
- Runs integrity check first — refuses if any file is tampered

### `revert <run_id>`
Restore all files to their original (v0) revision.

```
python pipeline_snapshot.py revert crumblingfacade_jackpot
```

- Copies v0 back to the project for every tracked file
- Sets active version back to v0 for all files
- Runs integrity check first

---

## Tamper Resistance

Every file stored in `.pipeline_snapshots/` has its SHA-256 hash recorded in the manifest. Here's what happens when something goes wrong:

| Scenario | Detection | Behavior |
|----------|-----------|----------|
| File edited externally | `verify` reports hash mismatch | `advance` and `revert` refuse to run |
| File deleted externally | `verify` reports missing file | `advance` and `revert` refuse to run |
| Sealed file tampered | `verify` reports `SEALED TAMPERED` | Same refusal, plus critical warning |
| Unauthorized file added | Not in manifest, ignored | `verify` doesn't check it, but `list` won't show it |

To test tamper detection yourself:

```bash
# After a pipeline run, manually edit a file in .pipeline_snapshots/
echo "tampered" >> .pipeline_snapshots/my_run/files/src_Engine.cpp/v1_20260429_170000

# Then run verify
python pipeline_snapshot.py verify my_run
# → ✗ my_run: INTEGRITY FAILURE
#     src/Engine.cpp rev1 [TAMPERED]: hash mismatch
```

---

## Python API (for pipeline.py integration)

The `SnapshotManager` class is used by `pipeline.py` internally, but you can also use it directly:

```python
from pipeline_snapshot import SnapshotManager

# Create a new snapshot run
snap = SnapshotManager("my-feature", "Add jackpot to plinko")

# Save originals before making changes
snap.save_originals(["src/Engine.cpp", "src/Engine.h"])

# Save proposals from agents
snap.save_proposal("C++ Core", 1, "src/Engine.cpp", new_content)
snap.save_proposal("Lua", 2, "attractions/plinko.lua", lua_content)

# Generate diffs
diff = snap.generate_diff("src/Engine.cpp")

# Apply to working tree
snap.apply_proposals()

# Revert to originals
snap.revert_all()

# Check integrity
ok, errors = snap.verify_integrity()

# Seal a version
snap.seal_version("src/Engine.cpp", 2)

# Checkout a specific version
snap.checkout_version("src/Engine.cpp", 1)
```

---

## Directory Structure

```
.pipeline_snapshots/
  crumblingfacade_jackpot_20260429/
    global_manifest.json          # Run metadata (description, tasks, applied status)
    files/
      src_Engine.cpp/
        file_manifest.json        # Per-file revision list with SHA-256 hashes
        v0_20260429_170000        # Original capture (never evicted)
        v1_20260429_171000        # Proposal v1
        v2_20260429_172000        # Proposal v2
        v3_20260429_173000        # Proposal v3 (oldest evicted on v4+)
      src_Engine.h/
        file_manifest.json
        v0_20260429_170000
        v1_20260429_171000
    raw_outputs/
      task1_C++_Core.txt          # Raw agent output (for reference)
      task2_Lua.txt
```

---

## Tips & Tricks

- **Seal early, seal often** — If you know a version is good, seal it. This prevents accidental eviction when new revisions come in.
- **Checkout before advance** — Use `checkout` to set which version you want, then `advance` to write it to the project.
- **Verify before trusting** — Run `verify` after any external tool touches your project files to make sure nothing was silently modified.
- **v0 is sacred** — The original capture is always preserved. No matter how many revisions you cycle through, you can always `revert` back to the starting point.
- **Run IDs are descriptive** — The pipeline auto-generates run IDs from the feature name + timestamp. You can also pass your own: `SnapshotManager("my-custom-id")`.
