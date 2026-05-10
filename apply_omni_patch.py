import os
import re

def apply_omni_patches():
    print("[System] Initiating deterministic VRAM & Telemetry patch...")

    # ---------------------------------------------------------
    # 1. Patching pipeline.py (Phase Timestamps)
    # ---------------------------------------------------------
    pipeline_path = 'pipeline.py'
    if os.path.exists(pipeline_path):
        with open(pipeline_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if 'from datetime import datetime' not in content:
            content = 'from datetime import datetime\n' + content

        content = re.sub(
            r'(print\(\s*[f]?"\s*)(Phase\s+\d+.*?)("\s*\))',
            r'\1[{datetime.now().strftime("%H:%M:%S")}] \2\3',
            content
        )

        with open(pipeline_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("  [+] Successfully patched pipeline.py (Telemetry)")
    else:
        print("  [-] Error: pipeline.py not found.")

    # ---------------------------------------------------------
    # 2. Patching mesh_loops.py (Task Start/End Timestamps)
    # ---------------------------------------------------------
    mesh_path = 'mesh_loops.py'
    if os.path.exists(mesh_path):
        with open(mesh_path, 'r', encoding='utf-8') as f:
            content = f.read()

        if 'from datetime import datetime' not in content:
            content = 'from datetime import datetime\n' + content

        content = re.sub(
            r'(print\(\s*[f]?"\s*)(.*?Calling Ollama.*?)("\s*\))',
            r'\1[{datetime.now().strftime("%H:%M:%S")}] [START] \2\3',
            content
        )

        generate_call_pattern = r"(response\s*=\s*ollama_client\.generate_text\([\s\S]*?stream=(?:True|False)\s*\))"
        end_marker_injection = r'\1\n            print(f"  [{datetime.now().strftime(\'%H:%M:%S\')}] [END] Task generation complete.")'
        
        if '[END] Task generation complete' not in content:
            content = re.sub(generate_call_pattern, end_marker_injection, content)

        with open(mesh_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("  [+] Successfully patched mesh_loops.py (Telemetry)")
    else:
        print("  [-] Error: mesh_loops.py not found.")

    # ---------------------------------------------------------
    # 3. Patching ollama_client.py (VRAM Eviction)
    # ---------------------------------------------------------
    client_path = 'ollama_client.py'
    if os.path.exists(client_path):
        with open(client_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Inject global state tracker
        if "_active_model = None" not in content:
            content = re.sub(r'(import .*?\n\n)', r'\1_active_model = None\n\n', content, count=1)
            if "_active_model = None" not in content:
                content = "import requests\n_active_model = None\n" + content

        # Inject eviction logic inside generate_text
        func_pattern = r'(def generate_text\([^)]+\):[^{]*?\n)(\s+)'
        eviction_logic = r"""\1\2global _active_model
\2if _active_model and _active_model != model:
\2    try:
\2        print(f"  [System] VRAM Flush: Evicting {_active_model} from unified memory...")
\2        url = OLLAMA_URL if 'OLLAMA_URL' in globals() else 'http://localhost:11434'
\2        requests.post(f"{url}/api/generate", json={"model": _active_model, "keep_alive": 0}, timeout=5)
\2    except Exception:
\2        pass
\2_active_model = model\n\n\2"""

        if "global _active_model" not in content:
            content = re.sub(func_pattern, eviction_logic, content, count=1)
            with open(client_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print("  [+] Successfully patched ollama_client.py (VRAM Eviction)")
        else:
            print("  [!] ollama_client.py already contains VRAM eviction logic.")
    else:
        print("  [-] Error: ollama_client.py not found.")

if __name__ == "__main__":
    apply_omni_patches()