# System Architecture Diagrams

## 1. Overall System Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                         KERNEL LAYER                                │
│                      (Framework/Agnostic)                           │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │ pipeline.py — Main orchestration entry point              │   │
│  │ • Cartridge discovery & mounting                           │   │
│  │ • Prompt bootstrap & refresh                              │   │
│  │ • Session state management                                │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                 │                                   │
│  ┌──────────────────────────────▼──────────────────────────────┐   │
│  │ _prompts.py — Generic prompt factories + bootstrap          │   │
│  │ • build_director_system()                                   │   │
│  │ • build_review_prompt()                                     │   │
│  │ • build_ledger_memory_rule()                               │   │
│  │ • pipeline_bootstrap_prompts()  ← Refresh from cartridge   │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                 │                                   │
│  ┌──────────────────────────────▼──────────────────────────────┐   │
│  │ domain_registry.py — Agent resolution & prompt assembly     │   │
│  │ • resolve_agent_name("cpp") → "CPP_EXPERT"                │   │
│  │ • get_agent_system(agent) → system prompt                 │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                 │                                   │
│  ┌──────────────────────────────▼──────────────────────────────┐   │
│  │ mesh_loops.py — Task execution, review/fix loops           │   │
│  │ • run_fetches()                                             │   │
│  │ • run_tasks()                                               │   │
│  │ • Wave sorting by model affinity                           │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                 ┌────────────────▼────────────────┐
                 │   CARTRIDGE LAYER               │
                 │  (Project-Specific)             │
                 └────────────────┬────────────────┘
                                  │
        ┌─────────────────────────┴──────────────────────────┐
        │                                                     │
   ┌────▼──────────────────┐              ┌─────────────────▼───┐
   │ UE4AgentCartridge     │              │MidwayAgentCartridge │
   │ ─────────────────────│              │──────────────────────│
   │ ECOSYSTEM_NAME       │              │ECOSYSTEM_NAME        │
   │ get_domain_registry()│              │get_domain_registry() │
   │ get_alias_map()      │              │get_alias_map()       │
   │ get_environment..()  │              │get_environment..()   │
   │ get_coding_mandates()│              │get_coding_mandates() │
   │ get_review_prompt..()│              │get_review_prompt..() │
   │ get_terminology..()  │              │get_terminology..()   │
   └─────────────────────┘              └──────────────────────┘
```

---

## 2. Cartridge Selection & Bootstrap Flow

```
START
  │
  ├─ User runs: python pipeline.run_mesh_pipeline("task")
  │
  └─► pipeline.py:run_mesh_pipeline()
       │
       ├─ ctx = _CTX (PipelineContext)
       │
       ├─► cartridge_loader.load_cartridge()
       │   │
       │   ├─ Check .pipeline_config.json for selected cartridge
       │   ├─ If not found, scan cartridges/ directory
       │   ├─ If multiple, offer wizard
       │   └─ RETURN: Selected cartridge class
       │
       ├─► ctx.mount_cartridge(SelectedCartridge)
       │   │
       │   ├─ domain_registry = SelectedCartridge.get_domain_registry()
       │   ├─ alias_map = SelectedCartridge.get_alias_map()
       │   ├─ environment_metadata = SelectedCartridge.get_environment_metadata()
       │   ├─ _cartridge_reasoning_gates = SelectedCartridge.get_reasoning_gate_domains()
       │   ├─ _cartridge_coding_mandates = SelectedCartridge.get_coding_mandates()
       │   ├─ _cartridge_review_extra = SelectedCartridge.get_review_prompt_extra()
       │   └─ _cartridge_terminology = SelectedCartridge.get_terminology_note()
       │
       ├─► _prompts.pipeline_bootstrap_prompts()
       │   │
       │   ├─ REASONING_GATE_DOMAINS = ctx._cartridge_reasoning_gates
       │   ├─ DIRECTOR_SYSTEM = build_director_system()  [with cartridge context]
       │   ├─ REVIEW_PROMPT = build_review_prompt()      [with cartridge content]
       │   ├─ LEDGER_MEMORY_RULE = build_ledger_memory_rule(coding_mandates)
       │   └─ [All module-level constants refreshed]
       │
       └─► PIPELINE READY WITH CARTRIDGE KNOWLEDGE
           • Agents have access to project-specific domains
           • Prompts contain project-specific mandates & terminology
           • Review criteria are project-specific
```

---

## 3. Agent Execution with Cartridge Knowledge

```
User Task: "cpp: Create a new ACharacter subclass for a player"
  │
  ├─► domain_registry.resolve_agent_name("cpp")
  │   • Look up in cartridge-supplied alias_map
  │   • "cpp" → "CPP_EXPERT"
  │
  ├─► domain_registry.get_agent_system("CPP_EXPERT")
  │   • Fetch domain config for CPP_ENGINE
  │   • Get system prompt template
  │   • Inject cartridge knowledge:
  │     ├─ Coding mandates (UE4-specific C++ standards)
  │     ├─ Type conventions (U prefix for classes, b for bools)
  │     ├─ Memory management rules (NewObject, Destroy)
  │     ├─ Replication patterns (DOREPLIFETIME, RPC)
  │     └─ Common pitfalls & warnings
  │
  ├─► FINAL SYSTEM PROMPT CONTAINS:
  │   • Director role description
  │   • Project name (from cartridge.ECOSYSTEM_NAME)
  │   • Domain-specific expertise (CPP domains)
  │   • Coding mandates (2,700+ chars of UE4 rules)
  │   • Examples & patterns
  │   • Safety checks & constraints
  │
  ├─► Agent processes task with full UE4 context
  │   • Generates C++ code following UE4 conventions
  │   • Uses proper naming (AActor, UComponent, etc.)
  │   • Respects memory management (NewObject, Destroy)
  │   • Includes replication declarations where needed
  │
  └─► Code submitted for review
      ├─► Review with UE4-specific criteria (from cartridge)
      │   • Check for UCLASS/UPROPERTY/UFUNCTION macros
      │   • Verify memory management correctness
      │   • Check naming conventions
      │   • Validate networking patterns
      │   └─ All checks from cartridge.get_review_prompt_extra()
      │
      └─► If fails: Agent re-executes with feedback
          └─ Loop until passes or max iterations reached
```

---

## 4. Cartridge Lifecycle & Acquisition

```
                      ┌─────────────────────────────┐
                      │  INITIAL STUB STATE         │
                      │                             │
                      │  11 domains                 │
                      │  53 aliases                 │
                      │  Placeholder mandates       │
                      │  Generic terminology        │
                      │                             │
                      │  ✓ Functional but basic     │
                      └────────────┬────────────────┘
                                   │
                                   ▼
                      ┌─────────────────────────────┐
              ╭──────►│  PHASE 1: SOURCES           │
              │       │  Identify knowledge sources │
              │       │  Scope: Tier 1/2/3          │
              │       │  1 week effort              │
              │       └────────────┬────────────────┘
              │                    │
    wizard.py │                    ▼
   (6-phase  │       ┌─────────────────────────────┐
    guide)   │       │  PHASE 2: SCRAPING          │
              │       │  Harvest 500+ API items     │
              │       │  Build: api_scraper.py      │
              │       │  1-2 week effort            │
              │       └────────────┬────────────────┘
              │                    │
              │                    ▼
              │       ┌─────────────────────────────┐
              │       │  PHASE 3: CONSTRAINTS       │
              │       │  Extract 50+ dangerous APIs │
              │       │  Build: constraint_db       │
              │       │  1-2 week effort            │
              │       └────────────┬────────────────┘
              │                    │
              │                    ▼
              │       ┌─────────────────────────────┐
              │       │  PHASE 4: KNOWLEDGE GRAPH   │
              │       │  Index 500+ items           │
              │       │  Build: knowledge_indexer   │
              │       │  1-2 week effort            │
              │       └────────────┬────────────────┘
              │                    │
              │                    ▼
              │       ┌─────────────────────────────┐
              │       │  PHASE 5: INTEGRATION       │
              │       │  Enrich cartridge methods   │
              │       │  Real knowledge in prompts  │
              │       │  1 week effort              │
              │       └────────────┬────────────────┘
              │                    │
              │                    ▼
              │       ┌─────────────────────────────┐
              │       │  PHASE 6: FEEDBACK LOOP     │
              │       │  Monitor agent success      │
              │       │  Capture failures           │
              │       │  Iterative improvement      │
              │       │  Ongoing                    │
              │       │                             │
              │       │  ✓ Production ready        │
              │       │  ✓ 80%+ success rate       │
              │       └─────────────────────────────┘
              │
              └──────────────────────────────────────
```

---

## 5. Knowledge Acquisition Phases

```
PHASE 1: SOURCE IDENTIFICATION (1 week)
┌────────────────────────────────────────────┐
│ What:  Identify & prioritize sources       │
│ Where: docs.unrealengine.com, GitHub, etc. │
│ Input: Wizard guidance                      │
│ Tools: acquisition_wizard.py               │
│ Output: Source list + priority scope       │
│ Start: NOW                                  │
└────────────────────────────────────────────┘
                     │
                     ▼
PHASE 2: WEB SCRAPING (1-2 weeks)
┌────────────────────────────────────────────┐
│ What:  Extract API docs & code examples    │
│ Where: docs.unrealengine.com + GitHub      │
│ Input: Source list from Phase 1             │
│ Tools: api_scraper.py, source_analyzer.py  │
│ Output: 500+ knowledge items (JSON/CSV)    │
│ Target: AActor, APawn, UComponent, etc.    │
└────────────────────────────────────────────┘
                     │
                     ▼
PHASE 3: CONSTRAINTS & RULES (1-2 weeks)
┌────────────────────────────────────────────┐
│ What:  Dangerous APIs + safety rules       │
│ Where: Documentation + analysis            │
│ Input: Knowledge items from Phase 2         │
│ Tools: constraint_validator.py             │
│ Output: Blacklist + limits database        │
│ Examples: Dangerous (malloc, delete)       │
│           Safe (NewObject, Destroy)        │
└────────────────────────────────────────────┘
                     │
                     ▼
PHASE 4: KNOWLEDGE GRAPH (1-2 weeks)
┌────────────────────────────────────────────┐
│ What:  Index & organize all knowledge      │
│ Where: docs/ue4_knowledge/ (JSON files)    │
│ Input: Items + rules from phases 2-3        │
│ Tools: knowledge_indexer.py                │
│ Output: Queryable graph (domain, API, etc) │
│ Format: JSON (or SQLite for scale)         │
└────────────────────────────────────────────┘
                     │
                     ▼
PHASE 5: CARTRIDGE INTEGRATION (1 week)
┌────────────────────────────────────────────┐
│ What:  Enrich cartridge with real knowledge│
│ Where: cartridges/ue4_ecosystem.py         │
│ Input: Knowledge graph from Phase 4         │
│ Tools: prompt_enhancer.py                  │
│ Output: Updated cartridge methods with:    │
│         • 200+ API references              │
│         • 500+ real coding rules           │
│         • Domain-specific checklists       │
│         • Real examples & patterns         │
└────────────────────────────────────────────┘
                     │
                     ▼
PHASE 6: CONTINUOUS LEARNING (Ongoing)
┌────────────────────────────────────────────┐
│ What:  Feedback loop & improvement         │
│ Where: Runtime agent execution             │
│ Input: Compilation errors, review feedback │
│ Tools: feedback_logger.py, metrics_dash    │
│ Output: Gaps identified, knowledge updated │
│ Metrics: 80%+ compilation, 90%+ review     │
└────────────────────────────────────────────┘
```

---

## 6. File Organization

```
midway-pipeline/
│
├── cartridges/
│   ├── __init__.py
│   ├── ue4_ecosystem.py ................ ✅ NEW - UE4 cartridge (11 domains)
│   └── midway_ecosystem.py ............ ✅ REFERENCE - Midway cartridge
│
├── cartridge_loader.py ............... ✅ NEW - Discovery & selection
├── acquisition_wizard.py ............. ✅ NEW - 6-phase interactive guide
├── verify_system.py .................. ✅ NEW - Health check & status
│
├── pipeline.py ...................... ✅ UPDATED - Mount cartridge, bootstrap
├── models.py ........................ ✅ UPDATED - Extended mount_cartridge()
├── _prompts.py ...................... ✅ UPDATED - Generic factories + bootstrap
│
├── docs/
│   ├── UE4_MASTER_INDEX.md ........... ✅ NEW - Navigation & decisions
│   ├── UE4_SOLID_PLAN.md ............ ✅ NEW - Complete strategy
│   ├── UE4_CARTRIDGE_README.md ...... ✅ NEW - Quick reference
│   ├── ACQUISITION_PHASE_PLAN.md .... ✅ NEW - Detailed roadmap
│   ├── COMPLETION_SUMMARY.md ........ ✅ NEW - What was delivered
│   ├── ARCHITECTURE_DIAGRAMS.md ..... ✅ NEW - THIS FILE
│   └── ue4_knowledge/ ............... ⏳ FUTURE - Knowledge graph
│       ├── cpp_engine/
│       ├── networking/
│       └── [other domains]/
│
├── CARTRIDGE_GUIDE.md ................ ✅ NEW - How to add cartridges
├── .pipeline_config.json ............ 📝 CREATED - User's selection
│
├── [Other existing files unchanged]
│
└── [Tools to build in Phase 2-6]
    ├── api_scraper.py ............... ⏳ TO BUILD (Phase 2)
    ├── source_analyzer.py ........... ⏳ TO BUILD (Phase 2)
    ├── constraint_validator.py ...... ⏳ TO BUILD (Phase 3)
    ├── knowledge_indexer.py ......... ⏳ TO BUILD (Phase 4)
    ├── prompt_enhancer.py ........... ⏳ TO BUILD (Phase 4-5)
    ├── feedback_logger.py ........... ⏳ TO BUILD (Phase 6)
    └── metrics_dashboard.py ......... ⏳ TO BUILD (Phase 6)
```

---

## 7. Success Criteria Checklist

```
✅ FOUNDATION (Now)
   [✓] UE4 cartridge created & functional
   [✓] Kernel/cartridge separation implemented
   [✓] Cartridge loader working
   [✓] Acquisition wizard available
   [✓] All documentation written

⏳ PHASE 1 (Week 1)
   [ ] Sources identified & prioritized
   [ ] Scope chosen (Tier 1, 1+2, or all)
   [ ] Wizard Phase 1 completed

⏳ PHASES 2-3 (Weeks 2-5)
   [ ] 500+ knowledge items harvested
   [ ] api_scraper.py built & working
   [ ] source_analyzer.py built & working
   [ ] 50+ dangerous APIs identified
   [ ] 100+ safe patterns documented
   [ ] Constraint database created

⏳ PHASE 4 (Weeks 6-7)
   [ ] Knowledge graph schema defined
   [ ] All items indexed & queryable
   [ ] knowledge_indexer.py working
   [ ] Organization by domain complete

⏳ PHASE 5 (Week 8)
   [ ] get_domain_registry() enriched
   [ ] get_coding_mandates() updated
   [ ] get_review_prompt_extra() populated
   [ ] get_terminology_note() enhanced
   [ ] Cartridge validated with real agents

⏳ PHASE 6 (Ongoing)
   [ ] Feedback capture online
   [ ] Metrics dashboard showing live data
   [ ] Agent success rate ≥ 75%
   [ ] Agent success rate ≥ 80%
   [ ] Review pass rate ≥ 90%
   [ ] Knowledge completeness ≥ 95%
```

---

## Timeline Visualization

```
Timeline: 8-12 weeks
Effort: 10-15 hrs/week

Week  │ Phase │ Key Deliverable
─────┼───────┼────────────────────────────────────────
  1  │   1   │ ███ Source identification complete
  2  │   2   │ ███░░░ API scraper POC built
  3  │   2   │ ███████ Scraping 200+ items
  4  │   3   │ ███████ Constraint database
  5  │   3   │ ███████░░ Safety rules extracted
  6  │   4   │ ███████░░░ Knowledge graph schema
  7  │   4   │ ███████░░░░ 500+ items indexed
  8  │   5   │ ███████░░░░░ Cartridge enriched
  9  │   6   │ ███████░░░░░░ Feedback loop online
 10  │   6   │ ███████░░░░░░░ First iteration improvements
 11  │   6   │ ███████░░░░░░░░ Success rate improving
 12  │   6   │ ███████████████ PRODUCTION READY
```

---

## Key Principle: Separation of Concerns

```
KERNEL KNOWS:
  ✓ How to parse tasks
  ✓ How to route to agents
  ✓ How to run reviews
  ✓ How to manage state
  ✓ Generic prompting techniques

KERNEL DOES NOT KNOW:
  ✗ What projects exist
  ✗ What domains matter
  ✗ Project-specific rules
  ✗ Safety constraints per project
  ✗ Coding standards for project

CARTRIDGE KNOWS:
  ✓ Project name (ecosystem)
  ✓ Domains for this project
  ✓ Agent aliases
  ✓ Coding mandates
  ✓ Review criteria
  ✓ Terminology & conventions

CARTRIDGE DOES NOT KNOW:
  ✗ How to orchestrate tasks
  ✗ How to manage pipeline state
  ✗ How to do reviews
  ✗ Generic prompting techniques
```

This separation means:
- **Add a new project?** Add a new cartridge, no kernel changes
- **Fix a kernel bug?** All projects benefit immediately
- **Change architecture?** Cartridges stay intact

---

**End of Architecture Diagrams**
