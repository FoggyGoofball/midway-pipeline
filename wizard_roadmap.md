# Autonomous Cartridge Wizard — Design Roadmap

## Purpose

Scan any project repository, intelligently detect its languages and frameworks,
autonomously harvest and format its API surface, and emit a complete, validated
pipeline cartridge — with zero manual authoring.

---

## What Already Exists (do not rebuild)

| File | Current State |
|---|---|
| `acquisition_wizard.py` | Interactive 6-phase shell wizard — placeholder UI, no real scanning or scraping |
| `cartridge_loader.py` | Loads a named cartridge class into the pipeline at runtime |
| `fetch_handler.py` | Paged context offload/retrieve — unrelated to API scraping |
| `cartridges/ue4_ecosystem.py` | Correct shell, empty domain knowledge — stub only |
| `cartridges/midway_data.py` | Fully populated cartridge — **gold-standard output format to target** |

---

## System Overview

```
/.../any-project/          (target repo — read-only scan)
		│
		▼
┌──────────────────────────────────────────────────┐
│  Stage 1 — PROJECT SCANNER                       │
│  Walk repo tree, detect languages, frameworks,   │
│  build systems, scripting layers, API surfaces   │
└───────────────────┬──────────────────────────────┘
					│  ProjectFingerprint
					▼
┌──────────────────────────────────────────────────┐
│  Stage 2 — API HARVESTER                         │
│  Per-language/framework: locate source headers,  │
│  online docs, local XML/JSON exports, SDL pages  │
│  → parse into raw APIEntry records               │
└───────────────────┬──────────────────────────────┘
					│  RawAPICorpus
					▼
┌──────────────────────────────────────────────────┐
│  Stage 3 — KNOWLEDGE COMPILER                    │
│  Deduplicate, cluster into domains, extract      │
│  signatures, deprecations, constraint rules,     │
│  phantom/forbidden names                         │
└───────────────────┬──────────────────────────────┘
					│  DomainKnowledgeGraph
					▼
┌──────────────────────────────────────────────────┐
│  Stage 4 — CARTRIDGE GENERATOR                   │
│  LLM-assisted prompt synthesis: write            │
│  domain prompts, reviewer rules, prohibitions,   │
│  bridge contract, alias map — emit .py cartridge │
└───────────────────┬──────────────────────────────┘
					│  cartridges/<project>_data.py
					▼
┌──────────────────────────────────────────────────┐
│  Stage 5 — SELF-VALIDATOR                        │
│  Syntax check, import check, contract round-trip │
│  test against runtime_sim, diff against          │
│  known-good midway cartridge for completeness    │
└──────────────────────────────────────────────────┘
```

---

## Stage 1 — Project Scanner

**New file:** `cartridge_wizard/scanner.py`

Walks a target repo root and emits a `ProjectFingerprint` dataclass.

### Detection Signals (priority order)

| Signal | Method |
|---|---|
| Language | File extension frequency map + shebang lines + linguist-style heuristics |
| Build system | Presence of `CMakeLists.txt`, `*.sln`, `*.vcxproj`, `Makefile`, `build.gradle`, `package.json`, `Cargo.toml`, `pyproject.toml`, etc. |
| Frameworks / engines | Marker files: `*.uproject` → UE, `*.godot` → Godot, `*.csproj` + Unity → Unity, `sol.hpp` → sol2/Lua bridge |
| Scripting layers | `.lua`, `.py`, `.js` ratios; `require`/`import` patterns |
| API surface files | `*.h`, `*.hpp`, `*.pyi`, `*.d.ts`, `*.xml` (VS doc comments), `*.json` (OpenAPI) |
| Existing docs | `docs/`, `Documentation/`, `API_Reference/` directories |
| Test frameworks | `gtest`, `catch2`, `pytest`, `jest` marker headers |

### Output: `ProjectFingerprint`

```python
@dataclass
class ProjectFingerprint:
	root: Path
	primary_language: str          # "cpp", "csharp", "python", ...
	secondary_languages: list[str]
	scripting_layers: list[str]    # ["lua", "python"]
	build_system: str              # "cmake", "msbuild", "cargo", ...
	frameworks: list[str]          # ["ue5", "sol2", "jolt"]
	api_header_paths: list[Path]   # .h/.hpp files with public API markers
	doc_paths: list[Path]          # existing doc roots
	confidence: float              # 0.0–1.0 detection confidence
```

---

## Stage 2 — API Harvester

**New file:** `cartridge_wizard/harvester.py`

Three parallel sub-harvesters feed a unified `RawAPICorpus`.

### 2a. Local Header Harvester (`LocalHeaderHarvester`)

- Reads `.h` / `.hpp` / `.pyi` / `.d.ts` from `api_header_paths`
- Parses function signatures, class names, enum values using `tree-sitter` (or regex fallback)
- Respects `// DEPRECATED`, `[[nodiscard]]`, `[[deprecated]]` annotations
- Emits: function name, signature, namespace, doc-comment, deprecation flag

### 2b. Web Doc Scraper (`WebDocScraper`)

- Per-framework URL map (seeded, user-extensible via `url_registry.json`):
  - UE5 → `dev.epicgames.com/documentation/en-us/unreal-engine/API/…`
  - Godot → `docs.godotengine.org/en/stable/classes/…`
  - Unity → `docs.unity3d.com/ScriptReference/…`
  - Generic C++ lib → cppreference, GitHub releases page
- Uses `httpx` (async) + `BeautifulSoup` for structured extraction
- Respects `robots.txt`, rate-limits to 1 req/sec, caches to `cartridge_wizard/_cache/`
- Falls back to offline cache if network is unavailable
- Emits the same `APIEntry` records as 2a

### 2c. LLM Extraction Pass (`LLMExtractHarvester`)

- For files/pages that resist structured parsing (prose docs, wikis, README tables)
- Feeds 2 000-char chunks to the pipeline's existing `ollama_client.py`
- Prompt: *"Extract every function name, class, enum, and constant from this text as JSON. Include deprecation notes."*
- Merges with structured results, deduplicates by signature hash

### Output: `RawAPICorpus`

```python
@dataclass
class APIEntry:
	name: str
	namespace: str
	signature: str
	description: str
	source: str          # "header" | "web" | "llm"
	deprecated: bool
	phantom: bool        # flagged during Stage 3 compilation
```

---

## Stage 3 — Knowledge Compiler

**New file:** `cartridge_wizard/compiler.py`

Transforms a flat `RawAPICorpus` into a structured `DomainKnowledgeGraph`.

### Steps

1. **Namespace clustering** — group entries by top-level namespace/module. Each cluster becomes a candidate domain (e.g. `MidwayPhysics.*` → `PHYSICS`, `Engine.*` → `ECONOMY`).
2. **Frequency analysis** — entries appearing in both headers *and* web docs receive higher confidence scores.
3. **Phantom detection** — entries found *only* in LLM extraction with no header or doc backing are flagged `phantom=True` and moved to the prohibitions list.
4. **Deprecation extraction** — `deprecated=True` entries become explicit *"DO NOT USE → use X instead"* rules.
5. **Constraint mining** — scan commit history and issue titles (if `.git` present) for recurring error patterns → seed the prohibitions list.
6. **Domain sizing** — clusters with >80 entries are split into sub-domains; clusters with <5 entries are merged with the nearest neighbor.

### Output: `DomainKnowledgeGraph`

```python
@dataclass
class Domain:
	name: str                      # "PHYSICS", "ECONOMY", "AUDIO"
	api_entries: list[APIEntry]
	prohibitions: list[str]        # phantom/deprecated names + rule text
	constraints: list[str]         # extracted from docs/commit history
	reviewer_rules: list[str]
	bridge_contract_section: str   # formatted markdown block
```

---

## Stage 4 — Cartridge Generator

**New file:** `cartridge_wizard/generator.py`

Takes a `DomainKnowledgeGraph` and emits a complete `cartridges/<slug>_data.py`
that matches the `midway_data.py` structure exactly.

### Template Strategy

- Uses a **fixed Python template** with `{PLACEHOLDER}` slots — the LLM never writes Python structure
- The LLM only fills **natural-language prompt text** inside string literals
- This guarantees the output is always valid Python and always importable

### What the LLM Fills (bounded, prompt-only)

| Slot | Content |
|---|---|
| `domain_persona` | *"You are an expert in X. Your role is…"* |
| `prohibitions_prose` | Converts phantom + deprecated entries to readable `P1. DO NOT…` rules |
| `reviewer_checklist` | Per-domain review items derived from the constraint list |
| `bridge_contract_text` | Formatted markdown summary of the approved API surface |

### What the Template Hard-codes (no LLM)

- Python class structure and `build_domain_registry()` signature
- `_SEARCH_REPLACE_MANDATE` (copied verbatim from midway cartridge)
- Domain dict keys, model assignments, alias lists (derived from fingerprint + knowledge graph)
- Argument-count table for `runtime_sim`-style validation

---

## Stage 5 — Self-Validator

**New file:** `cartridge_wizard/validator.py`

### Checks (fail-fast, in order)

1. `py_compile` — syntax clean, zero `SyntaxWarning`
2. `importlib.import_module` — actually importable
3. `build_domain_registry()` callable and returns a dict
4. Every domain contains: `system_prompt`, `user_prompt_template`, `review_prompt`, `prohibitions`
5. Every API named in the `bridge_contract` section exists in the `runtime_sim` API table — if not, emit a patch request back to Stage 3
6. No phantom names from the prohibitions list appear in the positive API sections (cross-contamination check)
7. Structural diff against `midway_data.py` — flag any missing top-level sections

**Pass** → write cartridge to `cartridges/<slug>_data.py` and register in `cartridge_loader.py`.  
**Fail** → emit a structured repair report back to Stage 4 for one re-generation pass.

---

## New Files to Create

```
cartridge_wizard/
	__init__.py
	scanner.py              Stage 1 — project detection
	harvester.py            Stage 2 — API extraction (all three sub-harvesters)
	compiler.py             Stage 3 — domain clustering + phantom detection
	generator.py            Stage 4 — cartridge template + LLM prompt fill
	validator.py            Stage 5 — syntax, import, contract round-trip checks
	url_registry.json       Seeded URL map for known frameworks
	_cache/                 Offline scrape cache (gitignored)

cartridge_wizard_cli.py     Top-level entry point (replaces acquisition_wizard stubs)
```

`acquisition_wizard.py` — replace placeholder phase stubs with calls into the new
stage pipeline, preserving the interactive resume/skip UX.

---

## Dependencies

| Library | Used for | Status |
|---|---|---|
| `httpx` | Async web scraping | Likely present (Ollama client) |
| `beautifulsoup4` | HTML doc parsing | Add if missing |
| `tree-sitter` + language packs | Robust C++/C#/Lua header parsing | Add if missing |
| `pathlib`, `dataclasses`, `re`, `json` | Scanner + compiler | stdlib |
| `ollama_client.py` | LLM extraction pass | ✅ Already present |

---

## Implementation Sequence

| Phase | Deliverable | Value |
|---|---|---|
| **Ph-1** | `scanner.py` + `ProjectFingerprint` + CLI dry-run | Proves detection works on real repos before any scraping |
| **Ph-2** | `LocalHeaderHarvester` | Highest-value, zero network dependency |
| **Ph-3** | `compiler.py` — namespace clustering + phantom detection | Core intelligence layer |
| **Ph-4** | `generator.py` — template + LLM prompt fill | First end-to-end cartridge output |
| **Ph-5** | `validator.py` | Closes the loop; makes output trustable |
| **Ph-6** | `WebDocScraper` + `url_registry.json` | Extends coverage to projects with no local headers |
| **Ph-7** | Wire into `acquisition_wizard.py` interactive shell | User-facing polish; replaces placeholder stubs |

Ph-1 through Ph-5 deliver a fully working wizard for **local-header-based projects**.  
Ph-6 extends to any project with online documentation.  
Ph-7 replaces the current placeholder acquisition wizard UI.

---

## Key Design Constraints

- **Read-only on the target repo** — the wizard never modifies the scanned project
- **LLM writes prose only** — Python structure is always template-driven; no LLM can corrupt the cartridge's import contract
- **Offline-first** — scrape cache means the wizard works without network after first run
- **Fail-safe validation** — a cartridge is never registered unless it passes all Stage 5 checks
- **Incremental** — each phase can be run independently; partial results are cached and resumed
