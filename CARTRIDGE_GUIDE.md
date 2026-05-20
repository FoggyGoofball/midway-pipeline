## Cartridge System & Project Selection

The pipeline now uses a **cartridge architecture** where project-specific rules, domains, and terminology live in self-contained cartridge modules. The kernel remains completely agnostic.

### Default Behavior

On first run, the pipeline automatically uses **Midway to Nowhere** as the default cartridge. The choice is persisted in `.pipeline_config.json` for future runs.

```json
{
  "cartridge": "MidwayAgentCartridge"
}
```

### Selecting a Different Project

To select a different cartridge at any time:

```bash
python cartridge_loader.py wizard
```

This interactive wizard will:
1. Scan the `cartridges/` directory
2. List all available cartridges
3. Prompt you to choose one
4. Save your selection to `.pipeline_config.json`

### Adding a New Project

To add a new project (cartridge):

1. **Create a new module** under `cartridges/`:
   ```
   cartridges/my_project.py
   ```

2. **Define a cartridge class** that implements the required interface:
   ```python
   class MyProjectAgentCartridge:
       """Agent ecosystem for My Project."""

       # Human-readable project name for prompts
       ECOSYSTEM_NAME = "My Project"

       @staticmethod
       def get_domain_registry():
           """Return { domain_name: domain_config_dict, ... }"""
           return { ... }

       @staticmethod
       def get_alias_map():
           """Return { agent_alias: agent_name, ... }"""
           return { ... }

       @staticmethod
       def get_environment_metadata():
           """Return { env_key: EnvironmentMetadata, ... }"""
           return { ... }

       # Optional: Project-specific prompt/rule content
       @staticmethod
       def get_reasoning_gate_domains():
           """Return set of domains that require reasoning-gate review."""
           return {"Domain1", "Domain2"}

       @staticmethod
       def get_coding_mandates():
           """Return critical API/language-specific rules as a string."""
           return "Your coding standards here..."

       @staticmethod
       def get_review_prompt_extra():
           """Return project-specific review criteria to append to base review prompt."""
           return "Additional review checks..."

       @staticmethod
       def get_terminology_note():
           """Return project-specific terminology and conventions."""
           return "In this project, X means Y..."
   ```

3. **Run the wizard** and select your new cartridge:
   ```bash
   python cartridge_loader.py wizard
   ```

### How It Works

- **Kernel (`_prompts.py`, `pipeline.py`, etc.)** — Generic, agnostic orchestration.
- **Cartridge (e.g., `midway_ecosystem.py`)** — Project-specific domains, rules, terminology, and agent prompts.
- **Loader (`cartridge_loader.py`)** — Discovers available cartridges and persists user selection.
- **Bootstrap (`pipeline_bootstrap_prompts()`)** — Refreshes all kernel prompt templates with cartridge content after mount.

The kernel never knows what project it's serving — only the cartridge knows that.
