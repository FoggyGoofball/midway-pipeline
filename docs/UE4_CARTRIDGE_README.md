# UE4 Agent Cartridge & Knowledge Acquisition Framework

## Quick Start

### 1. **Verify UE4 Cartridge is Available**

```bash
python -c "from cartridges.ue4_ecosystem import UE4AgentCartridge; print(UE4AgentCartridge.ECOSYSTEM_NAME)"
```

**Output:** `Unreal Engine 4`

### 2. **Select UE4 as Your Project Cartridge**

```bash
python cartridge_loader.py wizard
```

When prompted, select **"UE4AgentCartridge"** (option 2).

This creates `.pipeline_config.json`:
```json
{
  "cartridge": "UE4AgentCartridge"
}
```

### 3. **Start Knowledge Acquisition**

```bash
python acquisition_wizard.py
```

This launches an interactive 6-phase wizard to guide you through:
- Identifying knowledge sources (official docs, engine source, community)
- Scraping API documentation & code examples
- Building safety rule constraints
- Constructing a queryable knowledge graph
- Integrating knowledge into the cartridge
- Setting up continuous improvement feedback loops

---

## Architecture Overview

### Cartridge Structure

The UE4 cartridge (`cartridges/ue4_ecosystem.py`) provides:

**11 Agent Domains:**
- `CPP_ENGINE` — Native C++ engine modifications
- `BLUEPRINT` — Visual scripting & Blueprintable classes
- `ASSET` — Asset import, materials, content management
- `BUILD` — CMake, .Build.cs, project configuration
- `GAMEPLAY` — Game rules, controllers, pawns
- `UI` — UMG widgets, HUD, player interfaces
- `ANIMATION` — Skeletal meshes, animation blueprints
- `NETWORKING` — Replication, multiplayer, network code
- `PHYSICS` — Physics assets, collision, constraints
- `AUDIO` — Sound cues, dialogue, acoustic properties
- `DOCUMENTATION` — API docs, design docs, README

**53 Agent Aliases:** Flexible command entry (`"cpp"` → `"CPP_EXPERT"`, etc.)

**4 Environments:** Development, Shipping, Test

**Project-Specific Content:**
- `get_reasoning_gate_domains()` — Which domains need extra review
- `get_coding_mandates()` — Critical UE4 coding standards
- `get_review_prompt_extra()` — Domain-specific review checklists
- `get_terminology_note()` — UE4 terminology & conventions

### Knowledge Acquisition Phases

**Phase 1: Source Identification**
- Identify tier-1 (official), tier-2 (community), tier-3 (reference) sources
- Choose scope: official only, official+community, or comprehensive

**Phase 2: Web Scraping & Harvesting**
- Scrape docs.unrealengine.com for API documentation
- Mine github.com/EpicGames/UnrealEngine for patterns
- Extract code examples & best practices
- Target: 500+ knowledge items

**Phase 3: Constraint & Safety Rules**
- Identify 50+ dangerous APIs (what NOT to use)
- Document 100+ safe patterns
- Define performance limits (file sizes, memory, loops)
- Extract compilation flags & build constraints

**Phase 4: Knowledge Graph Construction**
- Build queryable index (JSON or SQLite)
- Organize by domain (CPP_ENGINE, NETWORKING, etc.)
- Enable fast lookups (API, domain, pattern)
- Support cross-references

**Phase 5: Cartridge Integration**
- Enrich `get_domain_registry()` with real API docs
- Update `get_coding_mandates()` with extracted rules
- Populate `get_review_prompt_extra()` with domain checklists
- Enhance `get_terminology_note()` with examples

**Phase 6: Continuous Learning**
- Monitor agent performance (compilation success rate)
- Capture feedback (what patterns work/fail)
- Iteratively improve knowledge graph
- Track metrics: completeness, success rate, gaps

---

## Current State (Stub Cartridge)

✅ **Implemented:**
- Full domain registry with 11 domains (C++, Blueprint, Asset, Build, etc.)
- 53 agent aliases for flexible command entry
- Placeholder coding mandates (UE4-specific best practices)
- Review prompt extras (safety checks, naming conventions)
- Terminology notes (UE4 concepts & conventions)
- Reasoning gate domains (high-risk areas requiring extra scrutiny)

⏳ **Next: Knowledge Acquisition (Phases 1-6)**
- Scrape official API docs
- Mine engine source for patterns
- Build comprehensive knowledge graph
- Integrate into cartridge prompts

---

## Key Decisions

### Storage Format (Phase 4)

**Option A: JSON** (recommended for now)
- Simple, human-readable
- Git-friendly for version control
- Good for < 10K items
- Directory structure: `docs/ue4_knowledge/{domain}/{item}.json`

**Option B: SQLite**
- Fast full-text search
- Better for > 10K items
- Harder to version control
- Option for later scaling

**Option C: Hybrid**
- JSON for source, SQLite for cached index
- Best of both worlds
- More complex setup

**Decision:** Start with **JSON**, migrate to hybrid if needed.

### API Version Targeting (Phase 1)

**Option A: Single Version** (Pin to 4.27 or 5.0)
- Simpler scraping & documentation
- Easier to maintain
- Recommended for MVP

**Option B: Multi-Version** (Track multiple versions with compatibility notes)
- More comprehensive
- More maintenance overhead
- Consider for v2

**Decision:** Start with **UE4.27 (latest 4.x)** as baseline.

### Agent Knowledge Access (Phase 6)

**Option A: Baked into Prompts** (current approach)
- Knowledge injected into system prompts at startup
- Simple, no runtime lookup overhead
- Good for knowledge < 50KB

**Option B: Tool-Based Lookup**
- Agents call knowledge lookup function during task execution
- Better for large knowledge graphs
- Adds latency per lookup

**Decision:** Start with **(A) baked-in**, add **(B) tools** if knowledge graph grows beyond 50KB.

---

## Integration Points

### Pipeline Bootstrap
When `pipeline.run_mesh_pipeline()` starts:
1. Cartridge is selected & mounted via `cartridge_loader.load_cartridge()`
2. `_prompts.pipeline_bootstrap_prompts()` refreshes all kernel prompts
3. Cartridge-supplied knowledge (coding mandates, domains, etc.) flows into agent system prompts
4. Agents execute tasks using enriched prompts

### Domain Resolution
When a user submits a task with alias `"cpp"`:
1. `domain_registry.resolve_agent_name("cpp")` → `"CPP_EXPERT"`
2. `domain_registry.get_agent_system("CPP_EXPERT")` → system prompt
3. Prompt includes cartridge-supplied `get_coding_mandates()` text
4. Agent receives full UE4 context in system prompt

### Review Loop
When code review happens:
1. `_finalize_review.py` runs review with cartridge-enhanced `REVIEW_PROMPT`
2. Review criteria include `get_review_prompt_extra()` domain-specific checks
3. If review fails, task routes back to original domain agent
4. Agent has full access to cartridge knowledge in system prompt

---

## Files & Locations

```
midway-pipeline/
├── cartridges/
│   ├── ue4_ecosystem.py              ← UE4 cartridge (stub, ready for enrichment)
│   └── midway_ecosystem.py           ← Original Midway cartridge (reference)
├── cartridge_loader.py               ← Cartridge discovery & selection
├── acquisition_wizard.py             ← Interactive 6-phase knowledge builder
├── .pipeline_config.json             ← User's cartridge selection (created by wizard)
├── docs/
│   ├── ACQUISITION_PHASE_PLAN.md     ← Detailed acquisition strategy
│   ├── UE4_CARTRIDGE_README.md       ← This file
│   └── ue4_knowledge/                ← Knowledge graph (created by acquisition)
│       ├── cpp_engine/
│       ├── networking/
│       └── [other domains]/
└── CARTRIDGE_GUIDE.md                ← How to add new cartridges
```

---

## Next Steps (Immediate)

1. **Run cartridge wizard:**
   ```bash
   python cartridge_loader.py wizard
   ```
   Select `UE4AgentCartridge`.

2. **Review acquisition plan:**
   ```
   Read: docs/ACQUISITION_PHASE_PLAN.md
   ```
   Understand the 6 phases & tool requirements.

3. **Start Phase 1 (Source Identification):**
   ```bash
   python acquisition_wizard.py
   → Select option "1"
   → Choose source scope (Tier 1, Tier 2, or comprehensive)
   ```

4. **Plan API scraper:**
   - Decide: JSON or SQLite storage?
   - Plan: Which classes to prioritize? (AActor, APawn, UComponent first?)
   - Build: `api_scraper.py` prototype (fetch single class from docs.unrealengine.com)

5. **Iterate through phases 2-6** as documented.

---

## Success Metrics

| Phase | Metric | Goal |
|-------|--------|------|
| 1 | Sources identified | All tier-1 sources located & accessible |
| 2 | Knowledge items | 500+ items harvested |
| 3 | Safety rules | 50+ dangerous APIs, 100+ safe patterns |
| 4 | Knowledge graph | Queryable index built & indexed |
| 5 | Cartridge enrichment | Prompts updated with real knowledge |
| 6 | Agent success | 80%+ compilation rate on generated code |

---

## Questions & Support

**Q: Can I use this while acquisition is in progress?**
A: Yes! The stub cartridge is fully functional. As acquisition phases complete, agents will have better knowledge.

**Q: How do I contribute knowledge?**
A: Either through the acquisition wizard (phases 2-3) or manually add JSON files to `docs/ue4_knowledge/`.

**Q: Can I switch back to Midway?**
A: Yes! Run `python cartridge_loader.py wizard` and select `MidwayAgentCartridge`.

**Q: What if I want to add a third cartridge?**
A: Create a new file in `cartridges/` (e.g., `unity_ecosystem.py`) with a class implementing the same interface as `UE4AgentCartridge`.

---

## References

- [Unreal Engine Documentation](https://docs.unrealengine.com)
- [Unreal Engine Source](https://github.com/EpicGames/UnrealEngine)
- [Unreal Online Learning](https://learn.unrealengine.com)
- [Cartridge Architecture Guide](CARTRIDGE_GUIDE.md)
- [Detailed Acquisition Plan](ACQUISITION_PHASE_PLAN.md)
