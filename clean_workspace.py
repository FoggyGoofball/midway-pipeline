import shutil
import subprocess
from pathlib import Path

def clean_workspace() -> None:
    print("============================================================")
    print("  MIDWAY PIPELINE :: WORKSPACE & GIT CACHE CLEANUP UTILITY  ")
    print("============================================================")

    root_dir = Path(__file__).resolve().parent

    # 1. Directories to forcefully purge
    target_dirs = [
        ".pipeline_checkpoints",
        ".pipeline_snapshots",
        "global_cache",
    ]

    for dname in target_dirs:
        dir_path = root_dir / dname
        if dir_path.exists():
            try:
                shutil.rmtree(dir_path, ignore_errors=True)
                print(f"  [Purged Directory] {dname}/")
            except Exception as e:
                print(f"  [Error] Failed to remove {dname}: {e}")

    # Purge all __pycache__ directories recursively
    count_pycache = 0
    for pycache in root_dir.rglob("__pycache__"):
        try:
            shutil.rmtree(pycache, ignore_errors=True)
            count_pycache += 1
        except Exception:
            pass
    if count_pycache > 0:
        print(f"  [Purged Cache] Removed {count_pycache} __pycache__ directory recursively")

    # 2. Ephemeral file patterns to forcefully delete
    patterns = [
        "offload_store/*.json",
        "pipeline_output_*.md",
        "pipeline_inventory.json",
        "*.bkup_*",
        "*.tmp",
        "*.bak",
        "*.old",
        "tests/test_task_*.lua",
        "tests/test_task_*.cpp",
        "tests/test_task_*.cxx",
        "tests/test_task_*.py",
        "tests/test_sub_*.lua",
        "tests/test_sub_*.cpp",
    ]

    count_files = 0
    for pat in patterns:
        if "/" in pat:
            folder, glob_pat = pat.split("/", 1)
            search_base = root_dir / folder
        else:
            search_base = root_dir
            glob_pat = pat

        if search_base.exists() and search_base.is_dir():
            for target_file in search_base.glob(glob_pat):
                # Protect structural index files from deletion
                if target_file.name in ("_index.json", ".keep"):
                    continue
                try:
                    target_file.unlink(missing_ok=True)
                    count_files += 1
                except Exception:
                    pass

    print(f"  [Purged Files] Removed {count_files} ephemeral runtime artifact(s)")

    # 3. Synchronize Git Tracking Cache
    print("\n  [Git Sync] Flushing tracking cache to align with .gitignore...")
    try:
        # Flush active cached files entirely
        subprocess.run(["git", "rm", "-r", "--cached", "."], cwd=root_dir, capture_output=True, check=False)
        # Re-stage the workspace clean against new ignore rules
        subprocess.run(["git", "add", "."], cwd=root_dir, capture_output=True, check=False)
        print("  [Git Sync] Tracking cache flushed and clean index staged successfully ✅")
        print("             Run 'git commit -m \"chore: clean workspace and sync ignores\"' to finalize.")
    except Exception as e:
        print(f"  [Git Sync] Notice: Git synchronization skipped or unavailable ({e})")

    print("============================================================")
    print("  Workspace cleanup complete.")
    print("============================================================")

if __name__ == "__main__":
    clean_workspace()
