# Autonomous Cartridge Wizard — Implementation Plan

> Saved from planning session, 2026-09-16.
> Premise: **code is knowable and quantifiable**, so documentation can be acquired and
> application/language-specific cartridges can be built autonomously.
> Goals: apply the pipeline to other projects, other models, and other hardware setups.

---

## 1. Current state (what exists vs. what is a stub)

| Piece | State |
|---|---|
| Kernel / cartridge split + loader + mount | ✅ done (`cartridge_loader.py`, mount path works) |
| `cartridges/midway_data.py` | ✅ gold-standard cartridge — the template to reproduce |
| `cartridges/ue4_ecosystem.py` | ⚠️ stub — correct shell, empty domain knowledge |
| `acquisition_wizard.py` | ⚠️ placeholder — "6-phase UI, no real scanning" |
| `wizard_roadmap.md` | ✅ full 5-stage design, **nothing implemented yet** |
| `structured_schemas.py` / `structured_client.py` / `serve_xgrammar.py` | ✅ built, partially wired |
| `lora generator/lora_fine_tune.py` | ✅ QLoRA scaffold exists |

The **cartridge architecture is done**. The **autonomous acquisition wizard is the missing piece** — it is specified in `wizard_roadmap.md` but not built.

---

## 2. The guiding principle (from pipeline hardening experience)

The highest-value thing a cartridge contains is **not prose — it is the machine-readable
API knowledge**: signatures, arity tables, and phantom-name prohibitions. Those are what
feed the deterministic validators that stopped the pipeline from death-spiraling:

- `midway_api_signatures.py` — arity tables (`SPAWN_ARITY`, `BODY_ARITY`, `ECONOMY_ARITY`)
- `runtime_sim.py` — runtime/arity/phantom-API analysis
- `_preflight_static.py` — phantom-API gate + `Fix G2` arity repair
- `contract_validator.py` — bridge-contract conformance

This maps exactly onto "code is knowable and quantifiable": **signatures, arities, and
deprecations are parseable from headers deterministically — no LLM required.** The LLM
should only write *prose* (personas, prohibitions phrased in English, review checklists),
never structure.

> Design rule (from `wizard_roadmap.md`): **the LLM never writes Python structure.**
> Python is always template-driven so a cartridge is always importable.

---

## 3. The plan — 5 stages + portability layer

### Phase 0 — Skeleton & CLI
- Create `cartridge_wizard/` package (`__init__.py`) + `cartridge_wizard_cli.py`.
- Replace `acquisition_wizard.py`'s stub phases with calls into the new stage pipeline,
  preserving the interactive resume/skip UX.
- No scanning yet — just the shell proving the pipeline runs.

### Phase 1 — Scanner (`scanner.py`)
Deterministic repo fingerprint; **no LLM, no network**.
- Languages: extension frequency + shebang lines.
- Build systems: `CMakeLists.txt`, `*.sln`, `*.vcxproj`, `Makefile`, `package.json`,
  `Cargo.toml`, `pyproject.toml`.
- Frameworks/engines: `*.uproject` → UE, `*.godot` → Godot, `sol.hpp` → sol2/Lua, etc.
- API-surface files: `.h/.hpp/.pyi/.d.ts/.xml/.json`.
- Output: `ProjectFingerprint { root, primary_language, secondary_languages,
  scripting_layers, build_system, frameworks, api_header_paths, doc_paths, confidence }`.

This is the "code is knowable" proof point and the first smoke test.

### Phase 2 — Local header harvester (`harvester.py`, local half)
Parse `.h/.hpp/.pyi/.d.ts` → `APIEntry { name, namespace, signature, arity, deprecated, doc }`.
- Regex parser first; tree-sitter optional upgrade.
- Respect `// DEPRECATED`, `[[deprecated]]`, `[[nodiscard]]`.
- **Compute arity here** — this is what the cartridge's validator tables need.

### Phase 3 — Compiler (`compiler.py`)
Transform flat corpus → `DomainKnowledgeGraph`.
- Namespace clustering → candidate domains.
- Cross-reference headers vs. docs for confidence scoring.
- **Phantom detection** — entries found only via LLM with no header/doc backing →
  prohibitions list.
- Deprecation extraction → "DO NOT USE → use X instead" rules.
- Emit the **arity table** and **prohibition list** — the machine-readable artifacts that
  make the new cartridge actually validate generated code.

### Phase 4 — Generator (`generator.py`)
Fixed Python template matching `midway_data.py` exactly.
- Hardcoded (no LLM): class shape, `build_domain_registry()`, alias map, arity tables,
  `_SEARCH_REPLACE_MANDATE` (copied verbatim).
- LLM fills only string literals: `domain_persona`, `prohibitions_prose`,
  `reviewer_checklist`, `bridge_contract_text`.
- Bounded, retryable.

### Phase 5 — Validator (`validator.py`)
Fail-fast, in order:
1. `py_compile` clean.
2. `importlib.import_module` importable.
3. `build_domain_registry()` callable and returns a dict.
4. Every domain has `system_prompt`, `user_prompt_template`, `review_prompt`, `prohibitions`.
5. Every API in the bridge-contract section exists in the runtime-sim arity table — else
   patch request back to Phase 3.
6. No phantom name from the prohibitions list appears in the positive API surface.
7. Structural diff against `midway_data.py` for missing sections.

**Pass** → register in `cartridge_loader.py`. **Fail** → one regeneration pass.

### Phase 6 — Web scraper (`harvester.py`, web half + `url_registry.json`)
Opt-in, offline-cached, rate-limited (1 req/s), respects `robots.txt`.
- `httpx` + `BeautifulSoup`; seeded URL map (UE5 / Godot / Unity / cppreference).
- Falls back to offline cache.
- **Only after the local path works** — this is the flaky, network-dependent part.

### Phase 7 — Wire into existing UX
- `acquisition_wizard.py` interactive shell + `cartridge_loader.py` selection.

---

## 4. Portability (other models, other hardware, other projects)

Mostly already ~80% present; needs to be made explicit.

- **Models:** the LLM-extraction pass calls `ollama_client.call_ollama` (model-agnostic).
  Add a `--model` CLI override + per-stage model budget (extraction = cheap model,
  prose-fill = coder model). Structure stages (1, 2, 3, 5) run with **no model at all**.
- **Hardware:** VRAM / num_ctx / quant decisions are already centralized in
  `ollama_config.py` (`resolve_ctx_size`, `KEEP_ALIVE`, model→ctx precedence). Extract a
  `hardware_profile` concept (e.g. `steamdeck-12gb`, `dgpu-16gb`) so the pipeline and
  wizard both read "what fits" from one place. `serve_xgrammar.py` already sketches the
  dGPU path — the profile just chooses Ollama vs vLLM/SGLang.
- **Other projects:** the only thing a new project needs is a cartridge — which the
  wizard now produces. `ue4_ecosystem.py` is the first real test case.

---

## 5. How this ties into pipeline hardening

The wizard's **arity table + phantom list** are exactly what
`midway_api_signatures.py`, `runtime_sim`, and `_preflight_static` consume today.

**Success test (quantifiable):**

> Run the wizard on repo X → get a cartridge → generate code for X →
> the code passes `runtime_sim`'s arity/phantom checks on the first try.

That is the measurable definition of "the cartridge is correct" — it closes the loop
between acquisition and validation.

---

## 6. New files to create

```
cartridge_wizard/
    __init__.py
    scanner.py            Stage 1 — project detection
    harvester.py          Stage 2 — API extraction (local + web + LLM)
    compiler.py           Stage 3 — domain clustering + phantom detection
    generator.py          Stage 4 — cartridge template + LLM prose fill
    validator.py          Stage 5 — syntax, import, contract round-trip checks
    url_registry.json     Seeded URL map for known frameworks
    _cache/               Offline scrape cache (gitignored)

cartridge_wizard_cli.py     Top-level entry point
```

---

## 7. Sequencing

| Phase | Deliverable | Value |
|---|---|---|
| Ph-0 | package skeleton + CLI | proves the pipeline runs |
| Ph-1 | `scanner.py` + `ProjectFingerprint` + dry-run | proves detection on real repos, zero network |
| Ph-2 | local header harvester | highest value, zero network dependency |
| Ph-3 | `compiler.py` | core intelligence layer |
| Ph-4 | `generator.py` | first end-to-end cartridge output |
| Ph-5 | `validator.py` | closes the loop; makes output trustable |
| Ph-6 | web scraper + `url_registry.json` | extends to doc-only projects |
| Ph-7 | UX wiring | replaces placeholder wizard |

**Ph-0 + Ph-1 are a self-contained, testable, no-network first deliverable** — they
fingerprint a repo and prove the premise on real code before any LLM/web work.

---

## 8. Key design constraints

- **Read-only on the target repo** — the wizard never modifies the scanned project.
- **LLM writes prose only** — Python structure is template-driven.
- **Offline-first** — scrape cache means the wizard works without network after first run.
- **Fail-safe validation** — a cartridge is never registered unless it passes all checks.
- **Incremental** — each phase runnable independently; partial results cached/resumed.
