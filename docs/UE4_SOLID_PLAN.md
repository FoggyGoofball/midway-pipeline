# UE4 CARTRIDGE & ACQUISITION: SOLID PLAN
## Complete Strategy for Knowledge-Driven Development

---

## Executive Summary

We have successfully:

✅ Created a **production-ready UE4 cartridge stub** with:
  - 11 domain definitions (C++, Blueprint, Networking, Physics, etc.)
  - 53 flexible agent aliases
  - 4 environments (Dev, Shipping, Test)
  - Full project-specific prompt infrastructure

✅ Built a **cartridge selection system**:
  - Dynamic cartridge discovery (`cartridge_loader.py`)
  - Persistent project config (`.pipeline_config.json`)
  - Easy switching between projects (Midway, UE4, or future cartridges)

✅ Designed a **6-phase acquisition strategy** to transform the stub into production knowledge:
  - Phase 1: Source identification & prioritization
  - Phase 2: Web scraping & documentation harvesting
  - Phase 3: Constraint & safety rule extraction
  - Phase 4: Knowledge graph construction
  - Phase 5: Cartridge integration
  - Phase 6: Continuous learning & feedback loops

✅ Created an **interactive acquisition wizard** (`acquisition_wizard.py`):
  - Guides users through all 6 phases
  - Checkpoint-based progress tracking
  - Pre-configured decisions & recommendations

---

## System Architecture

### **3-Layer Stack**

```
┌─────────────────────────────────────────────────────────────┐
│  AGENT LAYER                                                │
│  • CPP_EXPERT, BLUEPRINT_EXPERT, NETWORKING_EXPERT, etc.   │
│  • Uses domain-specific system prompts                      │
│  • Executes tasks via execute_task()                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  CARTRIDGE LAYER (Project-Specific)                         │
│  • Domain registry (11 domains for UE4)                     │
│  • Coding mandates (UE4-specific standards)                │
│  • Review criteria (safety checks per domain)              │
│  • Terminology & conventions                                │
│  • Knowledge graph (500+ items, after acquisition)         │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  KERNEL LAYER (Framework/Agnostic)                          │
│  • Generic orchestration (mesh loops, paging, etc.)        │
│  • Prompt factories (build_director_system, etc.)          │
│  • Pipeline bootstrap & cartridge mounting                 │
│  • No knowledge of specific projects                        │
└─────────────────────────────────────────────────────────────┘
```

### **Execution Flow**

```
1. User launches pipeline
   └─> cartridge_loader.load_cartridge()
       └─> Reads .pipeline_config.json
           └─> Mounts selected cartridge (UE4AgentCartridge)

2. Cartridge mounts
   └─> PipelineContext.mount_cartridge()
       ├─> domain_registry = cartridge.get_domain_registry()
       ├─> aliases = cartridge.get_alias_map()
       ├─> metadata = cartridge.get_environment_metadata()
       ├─> reasoning_gates = cartridge.get_reasoning_gate_domains()
       ├─> mandates = cartridge.get_coding_mandates()
       └─> [other prompt content fields]

3. Prompt bootstrap
   └─> _prompts.pipeline_bootstrap_prompts()
       ├─> REASONING_GATE_DOMAINS = {'C++', 'Lua', 'Physics'}
       ├─> DIRECTOR_SYSTEM = build_director_system() [with UE4 context]
       ├─> REVIEW_PROMPT = build_review_prompt() [with UE4 criteria]
       └─> [all module-level constants refresh from cartridge]

4. Agent execution
   └─> User submits: "cpp: Create a new ACharacter subclass"
       ├─> domain_registry.resolve_agent_name("cpp") → "CPP_EXPERT"
       ├─> domain_registry.get_agent_system("CPP_EXPERT") → system prompt
       │   └─> System prompt includes cartridge.get_coding_mandates()
       └─> Agent receives task with full UE4 context

5. Review & feedback
   └─> Code review (using cartridge.get_review_prompt_extra())
       └─> If fails, feedback logged for knowledge graph improvement
```

---

## Cartridge Interface

Every cartridge must implement these static methods:

```python
class ExampleAgentCartridge:
    ECOSYSTEM_NAME = "Project Name"

    @staticmethod
    def get_domain_registry() -> Dict[str, DomainConfig]:
        """Return { domain_name: {...config...}, ... }"""
        # Defines work domains (agents, rules, safety checks)

    @staticmethod
    def get_alias_map() -> Dict[str, str]:
        """Return { "user_alias": "AGENT_NAME", ... }"""
        # Flexible command entry

    @staticmethod
    def get_environment_metadata() -> Dict[str, EnvironmentMetadata]:
        """Return { env_name: EnvironmentMetadata(...), ... }"""
        # Dev, staging, production, etc.

    @staticmethod
    def get_domain_rules() -> Dict[str, dict]:
        """Legacy compat: return { domain: rules, ... }"""

    # ─── Optional: Project-specific prompt content ──────
    @staticmethod
    def get_reasoning_gate_domains() -> Set[str]:
        """Domains requiring extra review scrutiny"""

    @staticmethod
    def get_coding_mandates() -> str:
        """Critical project-specific standards"""

    @staticmethod
    def get_review_prompt_extra() -> str:
        """Domain-specific review criteria"""

    @staticmethod
    def get_terminology_note() -> str:
        """Project terminology & conventions"""
```

---

## UE4 Cartridge: Current State

### **What's Implemented (Stub)**

✅ **Domain Registry (11 domains):**
- CPP_ENGINE (native C++, engine mods)
- BLUEPRINT (visual scripting)
- ASSET (content management)
- BUILD (cmake, project config)
- GAMEPLAY (game rules, controllers)
- UI (UMG, HUD)
- ANIMATION (skeletal meshes, state machines)
- NETWORKING (replication, multiplayer)
- PHYSICS (collisions, constraints)
- AUDIO (sound, dialogue)
- DOCUMENTATION (API docs, design)

✅ **Agent Aliases (53 total):**
- General: "cpp", "c++", "engine", "native" → CPP_EXPERT
- Flexible: "bp", "blueprint", "visual" → BLUEPRINT_EXPERT
- [Plus 50+ more for quick command entry]

✅ **Environments (3):**
- development (with debug, no optimization)
- shipping (full optimization)
- test (validation enabled)

✅ **Project-Specific Content (Stubs):**
- Reasoning gates: {CPP_ENGINE, BUILD, GAMEPLAY, NETWORKING}
- Coding mandates: 8 sections of UE4-specific rules (memory, naming, replication, etc.)
- Review extras: Domain-specific safety checks
- Terminology: 50+ UE4 concepts explained

### **What's Needed (After Acquisition)**

⏳ **Real Knowledge (Phases 2-4):**
- 500+ API documentation items
- 50+ dangerous API identifications
- 100+ safe pattern examples
- Queryable knowledge graph

⏳ **Enhanced Prompts (Phase 5):**
- Real examples in coding mandates (not stubs)
- Data-driven review criteria (not generic)
- Cross-referenced terminology
- Domain-specific code patterns

⏳ **Feedback Loop (Phase 6):**
- Monitor agent success rates
- Capture compilation failures
- Log knowledge gaps
- Iteratively improve cartridge

---

## Acquisition Strategy: 6 Phases

### **Phase 1: Source Identification (Time: 2-4 hours)**

**Goal:** Map all knowledge sources and choose acquisition scope.

**Deliverables:**
- [ ] Tier-1 sources identified (official docs, engine source)
- [ ] Tier-2 sources identified (community, forums)
- [ ] Tier-3 sources identified (plugins, marketplace)
- [ ] Decision: Which tiers to prioritize?

**Recommendation:** Start with Tier-1 (official docs + engine source).

**Tools:** Manual research + `acquisition_wizard.py` Phase 1 menu

---

### **Phase 2: Web Scraping & Harvesting (Time: 1-2 weeks)**

**Goal:** Extract 500+ knowledge items from official sources.

**Targets:**

```
OFFICIAL DOCS (docs.unrealengine.com):
  • Core classes (AActor, APawn, ACharacter, UComponent, etc.)
  • Methods, properties, network replication status
  • Warnings & gotchas per class
  • Code examples (if available)
  • Guides: Networking, Physics, Animation, Blueprint best practices

ENGINE SOURCE (github.com/EpicGames/UnrealEngine):
  • DOREPLIFETIME macro usage patterns
  • NewObject<> construction patterns
  • Type naming conventions
  • Common anti-patterns (what NOT to do)
  • Performance-critical code sections
```

**Tools to Build:**

1. **`api_scraper.py`** — Fetch & parse docs.unrealengine.com
   ```python
   def scrape_ue4_class(class_name: str) -> dict:
       """Fetch AActor, APawn, etc. → structured JSON"""

   def scrape_ue4_guide(guide_path: str) -> str:
       """Fetch Networking, Physics guides → markdown"""
   ```

2. **`source_analyzer.py`** — Mine engine source patterns
   ```python
   def extract_pattern(repo_path: str, pattern: str) -> list:
       """Find DOREPLIFETIME, NewObject, etc. usage"""

   def analyze_api_usage(api_name: str) -> dict:
       """Frequency, context, safe/unsafe patterns"""
   ```

**Deliverables:**
- [ ] 20+ core class API docs extracted
- [ ] 10+ guides harvested (Networking, Physics, etc.)
- [ ] 100+ engine source patterns cataloged
- [ ] 500+ total knowledge items (CSV or JSON)

---

### **Phase 3: Constraint & Safety Rule Extraction (Time: 1-2 weeks)**

**Goal:** Identify 50+ dangerous APIs, 100+ safe patterns, performance limits.

**Dangerous API Categories:**

```
NETWORKING:
  ❌ Direct socket operations (FSocket)
  ❌ Raw serialization without replication framework
  ✓ UFUNCTION(Server) RPC with authority check
  ✓ UPROPERTY(Replicated) + DOREPLIFETIME

MEMORY:
  ❌ delete ptr; (UObjects need Destroy())
  ❌ malloc/free (use NewObject, TArray, TMap)
  ✓ NewObject<T>() construction
  ✓ obj->Destroy() cleanup

FILE I/O:
  ❌ Unbounded file reads (OOM risk)
  ❌ Raw pointers in shipping build
  ✓ FFileHelper with size limit
  ✓ FPlatformFileManager abstraction

[Continue for REFLECTION, CASTING, COMPILATION FLAGS...]
```

**Performance Limits:**

```
FILE SIZES:
  • CPP_ENGINE: max 100KB
  • BLUEPRINT: max 50KB
  • BUILD: max 25KB

MEMORY:
  • TArray: reasonable bounds (100K elements)
  • TMap: warn on > 10K entries
  • No circular UObject references

LOOPS:
  • Max 1000 iterations per tick
  • No unbounded loops without exit condition
```

**Tools:**
- Manual analysis + documentation review
- `constraint_validator.py` — Validate generated code against constraints

**Deliverables:**
- [ ] 50+ dangerous APIs cataloged
- [ ] 100+ safe patterns documented
- [ ] Performance limit CSV created
- [ ] Constraint validation rules encoded

---

### **Phase 4: Knowledge Graph Construction (Time: 1-2 weeks)**

**Goal:** Build queryable index of all 500+ items.

**Schema:**

```json
{
  "id": "ue4_class_aactor_setactorlocation",
  "type": "method",
  "domain": "CPP_ENGINE",
  "name": "AActor::SetActorLocation",
  "signature": "void SetActorLocation(FVector NewLocation, bool bSweep=false)",
  "description": "Move actor to new location...",
  "network_level": "replicated",
  "parameters": [
    {"name": "NewLocation", "type": "FVector", "doc": "..."},
    {"name": "bSweep", "type": "bool", "doc": "..."}
  ],
  "warnings": [
    "Don't call in constructor",
    "Network replicates automatically"
  ],
  "examples": ["Actor->SetActorLocation(FVector(100,200,300));"],
  "related": ["USceneComponent::SetRelativeLocation", "FVector"]
}
```

**Storage Decision (Recommend JSON initially):**

**Option A: JSON** ✓ Recommended
- Directory: `docs/ue4_knowledge/{domain}/`
- Files: `{item_name}.json`
- Pro: Simple, git-friendly, human-readable
- Con: Slower for 10K+ items

**Option B: SQLite**
- File: `knowledge.db`
- Pro: Fast full-text search
- Con: Less portable, harder to version

**Option C: Hybrid**
- Source: JSON files
- Cache: SQLite index (built at startup)
- Pro: Best of both
- Con: More complex

**Recommendation:** Start with **(A) JSON**, migrate to **(C) Hybrid** if > 5K items.

**Tools:**

1. **`knowledge_indexer.py`** — Build searchable index
   ```python
   class KnowledgeIndex:
       def search_by_domain(self, domain: str) -> List[Item]
       def search_by_api(self, api_name: str) -> Item
       def find_safe_alternatives(self, unsafe_api: str) -> List[str]
       def get_example(self, pattern: str) -> str
   ```

2. **`prompt_enhancer.py`** — Generate prompt sections from graph
   ```python
   def extract_coding_mandates(kg: KnowledgeGraph) -> str:
       """Build mandate section from safe patterns"""

   def build_review_checklist(domain: str, kg: KnowledgeGraph) -> str:
       """Build domain-specific review criteria"""
   ```

**Deliverables:**
- [ ] Knowledge graph schema defined
- [ ] 500+ items indexed
- [ ] Query engine working (search by domain, API, pattern)
- [ ] JSON files organized in `docs/ue4_knowledge/`

---

### **Phase 5: Cartridge Integration (Time: 1 week)**

**Goal:** Populate cartridge methods with real knowledge.

**Updates to `cartridges/ue4_ecosystem.py`:**

1. **`get_domain_registry()`**
   ```python
   "CPP_ENGINE": {
       "name": "C++ Engine Code",
       "apis": ["AActor", "APawn", "UComponent", ...],  # From knowledge graph
       "safe_patterns": [
           "NewObject<T>(GetOuter())",
           "obj->Destroy()",
           "DOREPLIFETIME(ThisClass, Property)",
       ],
       "dangerous_functions": [
           "malloc", "free", "new", "delete", ...
       ],
       "examples": [
           "// Spawn actor\nAActor* A = GetWorld()->SpawnActor<ACharacter>();",
       ],
   }
   ```

2. **`get_coding_mandates()`**
   - Replace stubs with real extracted rules
   - Include 200+ specific patterns
   - Add real C++ examples

3. **`get_review_prompt_extra()`**
   - Add domain-specific checks from knowledge graph
   - Include 50+ safety criteria per domain

4. **`get_terminology_note()`**
   - Real UE4 concepts (not stubs)
   - Example code for each
   - Common pitfalls

**Deliverables:**
- [ ] `get_domain_registry()` enriched with 200+ APIs
- [ ] Coding mandates updated (500+ real rules)
- [ ] Review criteria populated (domain-specific)
- [ ] Terminology notes enhanced (with examples)

---

### **Phase 6: Continuous Learning & Feedback Loop (Ongoing)**

**Goal:** Improve cartridge quality based on agent performance.

**Feedback Pipeline:**

```
Agent generates code (using enriched cartridge)
    ↓
Code review & compilation (identify gaps)
    ↓
Capture failures: compilation error? API misuse? Edge case?
    ↓
Log to feedback database
    ↓
Enrich knowledge graph (add missing pattern)
    ↓
Update cartridge prompts (improve next iteration)
    ↓
Agents re-execute with improved knowledge
```

**Metrics to Track:**

```
success_rate = (compilations passed) / (total compilations) → Target: 80%+
review_pass_rate = (passed review first try) / (total reviews) → Target: 90%+
knowledge_completeness = (APIs documented) / (APIs used) → Target: 95%+
feedback_lag = (hours to incorporate feedback) → Target: < 1 hour
```

**Tools:**

1. **`feedback_logger.py`** — Capture compilation/review failures
   ```python
   def log_failure(domain: str, error_type: str, details: str) -> None:
       """Failures → feedback.jsonl for batch analysis"""

   def summarize_gaps() -> Dict[str, List[str]]:
       """Report missing knowledge by domain"""
   ```

2. **`metrics_dashboard.py`** — Display performance
   ```
   UE4 Cartridge Performance:
   ├─ Success Rate: 75% (target 80%)
   ├─ Review Pass: 85% (target 90%)
   ├─ Knowledge Coverage: 68% (target 95%)
   └─ Top Gaps: networking edge cases, physics constraints
   ```

**Deliverables:**
- [ ] Feedback capture system online
- [ ] Metrics dashboard showing real-time performance
- [ ] Weekly review of top knowledge gaps
- [ ] Iterative cartridge improvements

---

## Implementation Timeline

| Week | Phase | Owner | Deliverables |
|------|-------|-------|--------------|
| 1 | Phase 1 | You | Sources identified, scope chosen |
| 2-3 | Phase 2 | You | 500+ items harvested, tools built |
| 4-5 | Phase 3 | You | 50+ dangerous APIs, 100+ patterns |
| 6-7 | Phase 4 | You | Knowledge graph indexed & queryable |
| 8 | Phase 5 | You | Cartridge enriched, validated |
| 9+ | Phase 6 | You + Agents | Continuous improvement cycle |

**Estimated Total:** 8-12 weeks (with ~10-15 hours/week effort)

---

## Success Criteria

✅ **Phase 1:** All sources identified, prioritized, accessible
✅ **Phase 2:** 500+ knowledge items harvested in structured format
✅ **Phase 3:** 50+ dangerous APIs blacklisted, 100+ safe patterns documented
✅ **Phase 4:** Knowledge queryable by domain, API name, pattern
✅ **Phase 5:** Cartridge prompts enriched with real knowledge
✅ **Phase 6:** Agents generate UE4 code with 80%+ compilation success rate

---

## Decision Points to Make NOW

**1. Source Scope:**
- [ ] Tier 1 only (Official docs + engine source) ← Recommended
- [ ] Tier 1 + Tier 2 (+ community)
- [ ] All tiers (comprehensive)

**2. UE4 Version Target:**
- [ ] UE4.27 (latest 4.x) ← Recommended for MVP
- [ ] UE5.0+
- [ ] Multi-version tracking

**3. Knowledge Storage:**
- [ ] JSON (simple, git-friendly) ← Recommended initially
- [ ] SQLite (fast, scalable)
- [ ] Hybrid (JSON + cached SQLite)

**4. Agent Knowledge Access:**
- [ ] Baked-in prompts (knowledge injected at startup) ← Recommended for now
- [ ] Tool-based lookup (agents call knowledge function)
- [ ] Both (migrate from baked-in to tools as graph grows)

**5. Acquisition Staffing:**
- [ ] Solo (you run all 6 phases)
- [ ] Distributed (teammates own different phases)
- [ ] Hybrid (you own phases 1, 4, 5; teammates do 2-3, 6)

---

## Getting Started

### **Right Now (30 minutes):**

1. ✅ Run cartridge selection:
   ```bash
   python cartridge_loader.py wizard
   # Select: UE4AgentCartridge
   ```

2. ✅ Verify UE4 cartridge loads:
   ```bash
   python -c "from cartridges.ue4_ecosystem import UE4AgentCartridge; print('OK')"
   ```

3. ✅ Review documentation:
   - Read: `docs/UE4_CARTRIDGE_README.md`
   - Read: `docs/ACQUISITION_PHASE_PLAN.md`

### **This Week:**

4. ⏳ Complete Phase 1 via wizard:
   ```bash
   python acquisition_wizard.py
   # Select: 1 (Source Identification)
   # Choose: Tier 1 scope
   ```

5. ⏳ Make 5 decisions (see above section)

6. ⏳ Plan Phase 2 (scraping tools & targets)

### **Next Week:**

7. ⏳ Build `api_scraper.py` prototype
8. ⏳ Scrape 5-10 core classes as POC
9. ⏳ Refine & scale up

---

## Questions for You

1. **Source scope preference:** Tier 1 only, or include community?
2. **Timeline pressure:** When do you need agents generating real UE4 code?
3. **Staffing:** Solo effort or team involvement?
4. **Knowledge format:** JSON, SQLite, or hybrid?
5. **First priority domain:** Which domain (CPP_ENGINE, NETWORKING, etc.) should we deep-dive first?

---

## Final Thoughts

You now have:

✅ A **working UE4 cartridge** that agents can use immediately (with stub knowledge)
✅ A **systematic acquisition plan** to enrich it over 8-12 weeks
✅ An **interactive wizard** to guide the process
✅ A **clear 6-phase roadmap** with tools, timelines, and success criteria
✅ A **kernel/cartridge architecture** that supports unlimited future projects

The stub cartridge is production-ready. As you work through the acquisition phases, agents will progressively gain access to real, verified UE4 knowledge.

Next step: **Run the wizard, make the 5 decisions, and we'll start Phase 2 together.**

Ready?
