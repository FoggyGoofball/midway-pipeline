"""
Cartridge discovery, loading, and configuration management.

Scans the cartridges/ directory for cartridge classes, maintains
a project-level config file to persist the selected cartridge,
and provides interactive selection via a setup wizard.
"""

import json
import sys
from pathlib import Path
from typing import Any, Optional, Type, Dict, List


CARTRIDGES_DIR = Path(__file__).parent / "cartridges"
CONFIG_FILE = Path(__file__).parent / ".pipeline_config.json"
DEFAULT_CARTRIDGE = "MidwayAgentCartridge"


def _discover_cartridges() -> Dict[str, Type[Any]]:
    """
    Scan cartridges/ directory for modules and extract cartridge classes.
    Returns a dict mapping class name -> class object for all classes
    that end with "AgentCartridge" or "Cartridge".
    """
    cartridges: Dict[str, Type[Any]] = {}

    if not CARTRIDGES_DIR.is_dir():
        return cartridges

    for module_file in CARTRIDGES_DIR.glob("*.py"):
        if module_file.name.startswith("_"):
            continue

        module_name = module_file.stem
        try:
            # Dynamically import the module
            spec = __import__(f"cartridges.{module_name}", fromlist=["*"])

            # Scan for cartridge classes
            for attr_name in dir(spec):
                if attr_name.startswith("_"):
                    continue
                attr = getattr(spec, attr_name)
                if isinstance(attr, type) and (
                    attr_name.endswith("AgentCartridge") or 
                    (attr_name.endswith("Cartridge") and attr_name != "Cartridge")
                ):
                    cartridges[attr_name] = attr
        except Exception as e:
            print(f"  [Warning] Could not load cartridge from {module_name}: {e}")

    return cartridges


def _load_config() -> dict:
    """Load project config from .pipeline_config.json, or return empty dict."""
    if CONFIG_FILE.is_file():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception as e:
            print(f"  [Warning] Could not parse {CONFIG_FILE.name}: {e}")
    return {}


def _save_config(config: dict) -> None:
    """Persist config to .pipeline_config.json."""
    try:
        CONFIG_FILE.write_text(json.dumps(config, indent=2))
    except Exception as e:
        print(f"  [Warning] Could not save {CONFIG_FILE.name}: {e}")


def load_cartridge() -> Optional[Type[Any]]:
    """
    Load the selected cartridge class, or prompt for selection if not configured.

    Returns the cartridge class, or None if loading fails.
    """
    config = _load_config()
    cartridges = _discover_cartridges()

    if not cartridges:
        print("  [Error] No cartridges found in cartridges/ directory.")
        return None

    # Check if a cartridge is already configured
    selected_name = config.get("cartridge")
    if selected_name and selected_name in cartridges:
        try:
            return cartridges[selected_name]
        except Exception as e:
            print(f"  [Warning] Could not load configured cartridge '{selected_name}': {e}")
            # Fall through to selection

    # If no valid config, prompt user or use default
    if selected_name:
        print(f"  [Note] Configured cartridge '{selected_name}' not found.")

    # Try default first
    if DEFAULT_CARTRIDGE in cartridges:
        print(f"  [Info] Using default cartridge: {DEFAULT_CARTRIDGE}")
        return cartridges[DEFAULT_CARTRIDGE]

    # If no default, pick the first one
    first_name = list(cartridges.keys())[0]
    print(f"  [Info] Using first available cartridge: {first_name}")
    return cartridges[first_name]


def run_cartridge_wizard() -> None:
    """
    Interactive wizard to discover and select a cartridge.
    Saves the choice to .pipeline_config.json.
    """
    cartridges = _discover_cartridges()

    if not cartridges:
        print("No cartridges found in cartridges/ directory.")
        return

    print("\n" + "=" * 70)
    print("  CARTRIDGE SELECTOR")
    print("=" * 70)
    print(f"\nFound {len(cartridges)} cartridge(s):\n")

    sorted_names = sorted(cartridges.keys())
    for i, name in enumerate(sorted_names, start=1):
        cartridge_cls = cartridges[name]
        ecosystem = getattr(cartridge_cls, "ECOSYSTEM_NAME", "<unknown>")
        print(f"  {i}. {name:40s} [{ecosystem}]")

    print()
    while True:
        try:
            choice = input(f"Select cartridge (1-{len(sorted_names)}): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(sorted_names):
                selected = sorted_names[idx]
                config = _load_config()
                config["cartridge"] = selected
                _save_config(config)
                print(f"\n✓ Cartridge set to '{selected}'. Config saved to {CONFIG_FILE.name}.\n")
                return
            else:
                print(f"  Please enter a number between 1 and {len(sorted_names)}.")
        except ValueError:
            print(f"  Please enter a valid number.")
        except KeyboardInterrupt:
            print("\n  Cancelled.")
            return


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "wizard":
        run_cartridge_wizard()
    else:
        print("Usage: python cartridge_loader.py wizard")
