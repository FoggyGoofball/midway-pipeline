## ACQUISITION PHASE PLAN
### Knowledge Discovery & API Scraping for UE4 Cartridge

---

## Phase Overview

The **Acquisition Phase** is the process of transforming stub cartridges into production-ready knowledge bases. For UE4, we'll systematically gather:

1. **Official API documentation** (Unreal Engine API reference)
2. **Code examples & patterns** (engine source, community best practices)
3. **Constraint & safety rules** (compilation flags, memory limits, dangerous APIs)
4. **Domain expertise** (industry conventions, performance tuning, architecture)

This feeds into the cartridge's `get_*()` methods and the agent prompts.

---

## Acquisition Strategy

### **Phase 1: Source Identification & Prioritization**

**Goal:** Map out all knowledge sources and prioritize by value.

**Sources (Tier 1 — High Value):**
- [ ] **Official Unreal Engine Documentation** (docs.unrealengine.com)
  - C++ API Reference (FVector, AActor, UObject, etc.)
  - Blueprint Node Reference
  - Replication & Networking Guide
  - Physics & Collision Documentation
  - Blueprint Best Practices

- [ ] **Engine Source Code** (github.com/EpicGames/UnrealEngine)
  - Core classes (Actor.h, Pawn.h, Character.h)
  - Common patterns (NewObject, replication macros, RPC patterns)
  - Safe/unsafe API usage examples

- [ ] **Unreal Online Learning** (learn.unrealengine.com)
  - Architecture patterns (separation of concerns, state management)
  - Networking guide (authority, replication, ownership)
  - Performance optimization guide

**Sources (Tier 2 — Medium Value):**
- [ ] **Community Resources**
  - Unreal Engine Discord/Forums (common pain points, workarounds)
  - GitHub Unreal-community projects (real-world patterns)
  - Medium/Blog posts (expert opinions on architecture)

- [ ] **Company-Specific Knowledge** (if applicable)
  - Internal coding standards
  - Previous project postmortems
  - Team-discovered gotchas & lessons learned

**Sources (Tier 3 — Reference):**
- [ ] **Third-Party Plugins** (usage patterns, compatibility notes)
- [ ] **Console-Specific Docs** (Xbox, PlayStation certification rules)
- [ ] **Marketplace Assets** (common patterns, pitfalls)

---

### **Phase 2: Web Scraping & Documentation Harvesting**

**Goal:** Programmatically extract structured API/guide content.

#### **2a. Official Documentation Scraping**

**What to scrape from docs.unrealengine.com:**

```
Path Pattern: /API/Runtime/CoreUObject/UObject/
Target Classes:
  ├─ AActor (movement, networking, lifecycle)
  ├─ APawn (possession, control, input)
  ├─ ACharacter (animation, jumping, walking)
  ├─ AController (AI, player input)
  ├─ UComponent (attachment, replication)
  ├─ UActorComponent (lifecycle)
  ├─ UGameInstance (persistence)
  ├─ UWorld (level management)
  ├─ UGameMode / UGameState (rules, replication)
  └─ [Continue for each domain]

For each class, extract:
  • Full signature & namespace
  • Description & intended use
  • Public methods (with param types, return types)
  • Network properties (Replicated/NotReplicated)
  • Common pitfalls / warnings
  • Code examples (if available)
```

**Tool to Build:** `api_scraper.py`

```python
def scrape_ue4_class(class_name: str) -> dict:
    """
    Fetch class documentation from docs.unrealengine.com.
    Return parsed dict with signature, methods, properties, warnings.
    """
    # Construct URL
    # Parse HTML (BeautifulSoup or Playwright)
    # Extract structured data
    # Store in JSON

def scrape_ue4_guide(guide_path: str) -> str:
    """
    Fetch guide/tutorial content (Networking, Physics, etc.).
    Return markdown of best practices & patterns.
    """
```

#### **2b. Engine Source Mining**

**What to extract from github.com/EpicGames/UnrealEngine:**

```
Patterns to Find:
  ├─ Memory management (NewObject<>, Destroy())
  ├─ Replication declarations (DOREPLIFETIME macro usage)
  ├─ RPC patterns (Server_, Client_ function naming)
  ├─ Dangerous functions (what to avoid in generated code)
  ├─ Type conventions (naming prefixes: U, A, F, I, E, b)
  ├─ Common anti-patterns (what NOT to do)
  └─ Performance best practices (hot paths, optimization)

Example Search Queries:
  • "DOREPLIFETIME" (find all replication declarations)
  • "virtual void Tick" (find tick patterns)
  • "NewObject<" (find object creation patterns)
  • "#if WITH_EDITOR" (find platform-specific guards)
  • "ensure(" or "check(" (find assertion patterns)
```

**Tool to Build:** `source_pattern_analyzer.py`

```python
def extract_pattern(repo_path: str, pattern: str, file_filters: list) -> list:
    """
    Search engine source for pattern usage.
    Return list of (file, line_num, context) matches.
    """

def analyze_api_usage(api_name: str) -> dict:
    """
    Analyze usage of a specific API across engine source.
    Return frequency, typical context, safe/unsafe patterns.
    """
```

---

### **Phase 3: Constraint & Safety Rule Extraction**

**Goal:** Identify which APIs are safe for agent-generated code, which are dangerous, what limits apply.

#### **3a. Dangerous API Blacklist**

Extract from engine & documentation:

```
NETWORKING (RPCs):
  ❌ Direct socket operations (FSocket, FUDPSocket)
  ❌ Raw data serialization without replication framework
  ❌ Unvalidated server authority changes
  ✓ UFUNCTION(Server) RPC with authority check
  ✓ UPROPERTY(Replicated) with DOREPLIFETIME

MEMORY MANAGEMENT:
  ❌ delete ptr; (UObjects must use Destroy())
  ❌ malloc/free (use NewObject<>, TArray, TMap)
  ❌ TSharedPtr<> for UObjects (use TWeakObjectPtr)
  ✓ NewObject<T>() for constructing
  ✓ obj->Destroy() for cleanup

FILE I/O:
  ❌ Raw file pointers in shipping builds
  ❌ Unbounded file reads (can OOM)
  ✓ FFileHelper with size limits
  ✓ FPlatformFileManager for abstraction

REFLECTION:
  ❌ Missing UCLASS(), UPROPERTY(), UFUNCTION()
  ❌ Raw casts without IsA<>() check
  ✓ Cast<>() with null validation
  ✓ Proper GENERATED_BODY()
```

#### **3b. Compile Flags & Build Constraints**

Extract safe compilation flags:

```
ALLOWED FLAGS:
  ✓ -DWITH_EDITOR=1 (dev builds)
  ✓ -DTEST_BUILD=1 (test builds)
  ✓ -DNDEBUG (shipping builds)
  ✓ -O3 (optimization levels)

FORBIDDEN FLAGS:
  ❌ -DDISABLE_SAFETY_CHECKS
  ❌ -DALLOW_UNSAFE_POINTERS
  ❌ Custom defines that disable validation
```

#### **3c. Performance Limits**

Define safe boundaries:

```
FILE SIZES:
  • CPP_ENGINE: max 100KB (avoids bloated classes)
  • BLUEPRINT: max 50KB
  • CONFIG: max 25KB

LOOP BOUNDS:
  • No unbounded loops without explicit exit condition
  • Max 1000 iterations per tick function
  • Async tasks for heavy work (> 1ms)

MEMORY ALLOCATION:
  • TArray size capped to reasonable bounds (e.g., 100K elements)
  • Warn on TMap allocations > 10K entries
  • Check for leak patterns (circular references, forgotten cleanup)
```

---

### **Phase 4: Knowledge Graph Construction**

**Goal:** Build a structured knowledge database that agents can query.

#### **4a. Schema Design**

```python
KnowledgeItem = {
    "id": "ue4_class_aactor",
    "type": "class",  # class, function, pattern, rule, guide
    "name": "AActor",
    "domain": "CPP_ENGINE",
    "description": "Base class for all game objects...",
    "signature": "class AActor : public UObject",
    "methods": [
        {
            "name": "SetActorLocation",
            "signature": "void SetActorLocation(FVector NewLocation, bool bSweep = false)",
            "network_level": "replicated",  # unreplicated, replicated, server_only, client_only
            "doc": "Move this actor to a new location...",
        }
    ],
    "properties": [
        {
            "name": "Rotation",
            "type": "FRotator",
            "replicated": True,
        }
    ],
    "examples": [
        "Actor->SetActorLocation(FVector(100, 200, 300));"
    ],
    "warnings": [
        "Don't call in constructor — transform not initialized yet",
        "Network replicates automatically — no manual sync needed"
    ],
    "related": ["USceneComponent", "UPrimitiveComponent"],
}

KnowledgeGraph = {
    "classes": [...],
    "functions": [...],
    "patterns": [...],
    "rules": [...],
    "guides": [...],
}
```

#### **4b. Indexing & Query Engine**

```python
class KnowledgeIndex:
    def search_by_domain(self, domain: str) -> List[KnowledgeItem]:
        """Find all knowledge items relevant to a domain."""

    def search_by_api(self, api_name: str) -> KnowledgeItem:
        """Look up documentation for a specific API."""

    def find_safe_alternatives(self, unsafe_api: str) -> List[str]:
        """Given a dangerous API, suggest safe alternatives."""

    def get_example(self, pattern: str) -> str:
        """Fetch code example for a pattern."""

    def check_constraint(self, operation: str) -> dict:
        """Validate if an operation is allowed (file size, memory, networking, etc.)."""
```

---

### **Phase 5: Cartridge Population**

**Goal:** Feed harvested knowledge into the cartridge methods.

#### **5a. Enhance `get_domain_registry()`**

Add to each domain's config:

```python
"domain_config": {
    "name": "C++ Engine Code",
    "description": "...",
    "apis": [
        "AActor", "APawn", "ACharacter", "UComponent", ...
    ],
    "safe_patterns": [
        "NewObject<T>(GetOuter())",
        "obj->Destroy()",
        "DOREPLIFETIME(ThisClass, PropertyName)",
    ],
    "dangerous_functions": [
        "malloc", "free", "new", "delete",
        "raw socket operations", "unsafe casts",
    ],
    "examples": [
        "// Spawn an actor\nAActor* NewActor = GetWorld()->SpawnActor<ACharacter>(...);",
        "// Replicate a property\nDOREPLIFETIME(AMyCharacter, Health);",
    ],
}
```

#### **5b. Enhance Coding Mandates**

Update `get_coding_mandates()` with extracted rules:

```
Source: knowledge_graph["rules"]["networking"]
Source: knowledge_graph["rules"]["memory_management"]
Source: github_engine_patterns
Output: Updated mandate string with real patterns & examples
```

#### **5c. Create Review Templates**

Update `get_review_prompt_extra()` with domain-specific checks:

```
Source: knowledge_graph["safe_patterns"]
Source: knowledge_graph["dangerous_functions"]
Source: constraint_database
Output: Specific, data-driven review criteria
```

---

### **Phase 6: Continuous Learning & Feedback**

**Goal:** Improve cartridge over time as agents encounter real-world issues.

#### **6a. Agent Feedback Loop**

```
┌─────────────────────────────────────────┐
│ Agent Generates Code                    │
│ (Using cartridge prompts & knowledge)   │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Code Review & Compilation               │
│ (Identify missing knowledge gaps)       │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Capture Feedback                        │
│ (Store patterns: what worked/what failed)│
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ Enrich Knowledge Graph                  │
│ (Add new patterns, rules, examples)     │
└─────────────────────────────────────────┘
```

#### **6b. Metrics & Health Checks**

```python
CartridgeMetrics = {
    "knowledge_completeness": 0.75,  # % of known APIs documented
    "agent_success_rate": 0.85,       # % of generated code compiles
    "review_pass_rate": 0.90,         # % passing review on first pass
    "gaps": [
        "PHYS domain has < 5 examples",
        "Networking patterns missing edge cases",
    ],
}
```

---

## Implementation Roadmap

### **Week 1: Foundation**
- [ ] Build `api_scraper.py` (BeautifulSoup + Playwright for docs.unrealengine.com)
- [ ] Build `source_analyzer.py` (GitHub source pattern extraction)
- [ ] Create initial knowledge graph schema (JSON/SQLite)
- [ ] Scrape top 20 core classes (AActor, APawn, UComponent, etc.)

### **Week 2: Enrichment**
- [ ] Scrape Unreal guides (Networking, Physics, Animation)
- [ ] Extract engine source patterns (DOREPLIFETIME, NewObject usage)
- [ ] Build constraint database (file sizes, memory limits, dangerous APIs)
- [ ] Populate knowledge graph (500+ items)

### **Week 3: Cartridge Integration**
- [ ] Enhance `get_domain_registry()` with real API docs
- [ ] Update `get_coding_mandates()` with extracted rules
- [ ] Enrich `get_review_prompt_extra()` with specific checks
- [ ] Add examples to `get_terminology_note()`

### **Week 4: Validation & Feedback**
- [ ] Test agents on real UE4 tasks
- [ ] Measure compilation success rate
- [ ] Capture feedback & gaps
- [ ] Iteratively improve knowledge graph

---

## Tools to Build

### **1. api_scraper.py**
```
Input: List of API names (AActor, APawn, UComponent)
Process:
  - Fetch docs.unrealengine.com/API/{ClassName}
  - Parse HTML for signature, methods, properties, examples
  - Extract warnings & best practices
Output: JSON { class_name: {...} }
```

### **2. source_analyzer.py**
```
Input: GitHub repo path + pattern queries
Process:
  - Clone/update EpicGames/UnrealEngine
  - Search for patterns (DOREPLIFETIME, NewObject, etc.)
  - Extract context & build usage map
  - Identify anti-patterns
Output: JSON { pattern: { usage_count, examples, ... } }
```

### **3. constraint_validator.py**
```
Input: Generated code (C++ or config)
Process:
  - Check file size (matches domain limit)
  - Scan for dangerous functions
  - Validate memory allocation patterns
  - Check replication declarations
Output: { valid: bool, warnings: [...], errors: [...] }
```

### **4. knowledge_indexer.py**
```
Input: Scraped knowledge + feedback logs
Process:
  - Build in-memory graph
  - Support fast lookups (domain, API name, pattern)
  - Track metrics (coverage, success rates)
Output: Queryable KnowledgeIndex class
```

### **5. prompt_enhancer.py**
```
Input: Knowledge graph
Process:
  - Extract safe patterns from graph
  - Extract dangerous APIs from graph
  - Build domain-specific prompt sections
Output: Enhanced cartridge methods (coding mandates, review criteria)
```

---

## Questions & Decisions

**Q1: Should we create a wizard to guide knowledge acquisition?**
- Interactive tool to let users select which domains/APIs to prioritize
- Helpful for focusing effort on high-value knowledge first

**Q2: How do we handle API changes across engine versions?**
- Option A: Pin to a specific engine version (4.27, 5.0, etc.)
- Option B: Track multiple versions with compatibility notes
- Recommendation: Start with a single version, add multi-version support later

**Q3: Where do we store the knowledge graph?**
- Option A: JSON files in `docs/ue4_knowledge/`
- Option B: SQLite database for fast querying
- Recommendation: Start with JSON, migrate to DB if > 10K items

**Q4: Should agents have direct read access to the knowledge graph?**
- Option A: Bake knowledge into prompt text (current approach)
- Option B: Add knowledge lookup as a tool agents can call
- Recommendation: Both — start with (A), add (B) for large graphs

**Q5: How do we validate scraped content for accuracy?**
- Spot-check samples before using in production
- Keep human review loop for critical safety rules
- Add version pinning so we can trace back issues

---

## Success Criteria

✅ **Phase 1 (Source ID):** Document identified, prioritized, & accessible
✅ **Phase 2 (Scraping):** 500+ knowledge items harvested & normalized
✅ **Phase 3 (Constraints):** 50+ dangerous APIs identified, 100+ safe patterns documented
✅ **Phase 4 (Graph):** Queryable, indexed knowledge base built
✅ **Phase 5 (Cartridge):** Cartridge prompts enriched with real UE4 knowledge
✅ **Phase 6 (Feedback):** Agents can generate UE4 code with 80%+ compilation rate

---

## Next Immediate Steps

1. **Decision: Which sources to prioritize?** (docs.unrealengine.com, engine source, or both?)
2. **Decision: Knowledge storage format?** (JSON, SQLite, or hybrid?)
3. **Build api_scraper.py prototype** (fetch & parse a single class as POC)
4. **Set up GitHub fork** (for engine source mining)
5. **Create knowledge schema & start manual seeding** (jump-start with hand-curated items)

What's your preference on these decisions?
