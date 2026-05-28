"""
Pydantic models & typed enums — the shared data contract for the entire pipeline.
All state flows through these models. No async/await — purely synchronous.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field, ConfigDict


# ── Attraction Design Document ────────────────────────────────────────────────

class HandleDeclaration(BaseModel):
    """A named physics/object handle declared in the design doc."""
    name: str                          # e.g. "hBall"
    lua_type: str = "userdata"         # userdata | number | boolean | table
    owner_task: str = ""               # task ID that creates/owns this handle
    lifecycle: str = "OnLoad"          # OnLoadStatic | OnLoad | runtime
    description: str = ""


class EventEdge(BaseModel):
    """A directed event flow edge: source triggers target."""
    trigger: str                       # e.g. "IsSensorTriggered(hGate)"
    action: str                        # e.g. "economy:AddScore(10)"
    owner_task: str = ""


class AttractionDesign(BaseModel):
    """
    Pre-decomposition design document produced by mesh_architect.py.
    Stored on PipelineContext and injected into every agent and Director prompt
    so all agents share a single authoritative view of handles, lifecycle order,
    event flow, pool requirements, and economy hooks.
    """
    title: str = ""
    summary: str = ""
    handles: List[HandleDeclaration] = Field(default_factory=list)
    lifecycle_order: List[str] = Field(default_factory=list)   # ordered list of registration calls
    event_flow: List[EventEdge] = Field(default_factory=list)
    pool_requirements: Dict[str, int] = Field(default_factory=dict)  # pool_key -> min_count
    economy_hooks: List[str] = Field(default_factory=list)     # e.g. ["AddScore", "SetMultiplier"]
    feature_checklist: List[str] = Field(default_factory=list) # plain-English features to verify
    raw_json: str = ""                                          # original LLM output preserved

    def to_context_block(self) -> str:
        """Render a compact, prompt-friendly summary for injection into agent context."""
        parts = [f"## 🎯 Attraction Design: {self.title}"]
        if self.summary:
            parts.append(self.summary)
        if self.handles:
            parts.append("\n### Declared Handles")
            for h in self.handles:
                parts.append(f"  {h.name} ({h.lua_type}) — {h.description} [owner: {h.owner_task}, lifecycle: {h.lifecycle}]")
        if self.lifecycle_order:
            parts.append("\n### OnLoad Registration Order")
            for i, step in enumerate(self.lifecycle_order, 1):
                parts.append(f"  {i}. {step}")
        if self.event_flow:
            parts.append("\n### Event Flow")
            for e in self.event_flow:
                parts.append(f"  {e.trigger} → {e.action}")
        if self.pool_requirements:
            parts.append("\n### Pool Requirements")
            for k, v in self.pool_requirements.items():
                parts.append(f"  {k}: {v}")
        if self.economy_hooks:
            parts.append("\n### Economy Hooks: " + ", ".join(self.economy_hooks))
        if self.feature_checklist:
            parts.append("\n### Feature Checklist")
            for f in self.feature_checklist:
                parts.append(f"  - {f}")
        return "\n".join(parts)


# ── Integration Schema ────────────────────────────────────────────────────────

class SchemaHandleEntry(BaseModel):
    """A handle or shared variable declared by an agent into the live schema."""
    name: str
    declared_by: str     # task ID
    lua_type: str = "userdata"
    created_in: str = "OnLoad"   # lifecycle hook


class SchemaConflict(BaseModel):
    name: str
    declared_by: List[str]
    reason: str


class IntegrationSchema(BaseModel):
    """
    Live cross-agent coordination contract built incrementally as tasks complete.
    Agents read this before writing code so handle names, registration order, and
    shared state are consistent across the entire merged output.
    """
    handles: Dict[str, SchemaHandleEntry] = Field(default_factory=dict)
    onload_order: List[str] = Field(default_factory=list)   # task IDs in intended order
    onstep_subscribers: List[str] = Field(default_factory=list)  # task IDs registering OnStep
    shared_vars: Dict[str, str] = Field(default_factory=dict)    # varname -> owning task
    conflicts: List[SchemaConflict] = Field(default_factory=list)

    def declare_handle(self, entry: SchemaHandleEntry) -> Optional[SchemaConflict]:
        """Register a handle. Returns a SchemaConflict if the name is already taken by another task."""
        existing = self.handles.get(entry.name)
        if existing and existing.declared_by != entry.declared_by:
            conflict = SchemaConflict(
                name=entry.name,
                declared_by=[existing.declared_by, entry.declared_by],
                reason=f"Handle '{entry.name}' declared by both {existing.declared_by} and {entry.declared_by}",
            )
            self.conflicts.append(conflict)
            return conflict
        self.handles[entry.name] = entry
        return None

    def to_context_block(self) -> str:
        """Compact prompt-friendly rendering for agent injection."""
        parts = ["## 🔗 Integration Schema (read before writing — do not redefine these)"]
        if self.handles:
            parts.append("### Declared Handles")
            for h in self.handles.values():
                parts.append(f"  {h.name} ({h.lua_type}) — created in {h.created_in} by task {h.declared_by}")
        if self.onload_order:
            parts.append("### OnLoad Registration Order: " + " → ".join(self.onload_order))
        if self.onstep_subscribers:
            parts.append("### OnStep Subscribers: " + ", ".join(self.onstep_subscribers))
        if self.shared_vars:
            parts.append("### Shared Variables")
            for v, owner in self.shared_vars.items():
                parts.append(f"  {v} (owned by {owner})")
        if self.conflicts:
            parts.append("### ⚠ CONFLICTS — must be resolved before merge")
            for c in self.conflicts:
                parts.append(f"  {c.name}: {c.reason}")
        return "\n".join(parts)


class SignalType(str, Enum):
    QUERY = "QUERY"
    DELEGATE = "DELEGATE"
    RESULT = "RESULT"
    APPROVE = "APPROVE"
    REVISE = "REVISE"
    VETO = "VETO"
    OBJECT = "OBJECT"
    RECOURSE = "RECOURSE"
    CONSULT = "CONSULT"
    APPEAL = "APPEAL"
    MERGE = "MERGE"
    REJECT = "REJECT"
    MATH_EVAL = "MATH_EVAL"
    FETCH = "FETCH"
    READ_OFFLOADED = "READ_OFFLOADED"
    EXTRACT_SKELETON = "EXTRACT_SKELETON"
    FLUSH = "FLUSH"
    REQUEST_API = "REQUEST_API"


class OrchestrationConfig(BaseModel):
    """Decoupled boundary parameters dynamically injected via Cartridge layer."""
    ollama_host: str = "http://192.168.0.16:11434"
    # Qwen Coder 3.5 profile (9B) — uncomment when backend hardware supports it
    # coder_model: str = "qwen3.5:9b",
    coder_model: str = "qwen2.5-coder:7b"
    reviewer_model: str = "phi3:14b"
    analyst_model: str = "phi3:14b"
    fallback_reviewer_model: str = "llama3.1:8b-instruct-q4_K_M"
    pre_summarizer_model: str = "phi3.5:latest"  # 3.8B mini — compresses large context before phi3:14b review
    librarian_model: str = "llama3.1:8b-instruct-q4_K_M"
    syntax_gate_model: str = "qwen2.5-coder:1.5b"
    intent_classifier_model: str = "llama3.2:1b"
    director_model: str = "llama3.1:8b-instruct-q4_K_M"
    max_iterations: int = 3
    max_consensus_iterations: int = 3
    max_subtasks_per_agent: int = 5
    review_max_iterations: int = 3
    scope_file_limit: int = 5
    scope_line_limit: int = 400
    ollama_timeout: int = 600
    # Synchronize default schema boundaries with hardware execution targets
    # qwen3.5:9b was 16384; qwen2.5-coder:7b has more headroom
    # ollama_num_ctx: int = 16384
    ollama_num_ctx: int = 32768
    max_tokens: int = 12000


class MeshSignal(BaseModel):
    type: SignalType
    target: str
    content: str
    source: str
    timestamp: datetime = Field(default_factory=datetime.now)

    # Backward-compatible constructor supporting positional args (Pydantic v1 style).
    # The refactored code uses keyword args per Pydantic v2 requirements, but
    # characterization tests (test_signals.py) use positional args from the
    # original monolithic pipeline. This wrapper preserves the old test expectations.
    def __init__(self, *args, **kwargs):
        if args and not kwargs:
            # Positional args: (type, target, content, source)
            if len(args) == 4:
                kwargs = {
                    "type": args[0],
                    "target": args[1],
                    "content": args[2],
                    "source": args[3],
                }
            elif len(args) == 1 and isinstance(args[0], SignalType):
                kwargs = {"type": args[0], "target": "", "content": "", "source": ""}
        super().__init__(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "target": self.target,
            "content": self.content,
            "source": self.source,
        }


class ConsensusResult(BaseModel):
    verdict: str  # "APPROVED" | "CONFLICT" | "REVISE"
    merged_code: str
    explanation: str
    warnings: List[str] = []

    # Backward-compatible constructor supporting positional args (Pydantic v1 style).
    # Characterization tests (test_signals.py) use positional args from the original
    # monolithic pipeline. This wrapper preserves the old test expectations.
    def __init__(self, *args, **kwargs):
        if args and not kwargs:
            # Positional args: (verdict, merged_code, explanation, warnings)
            field_names = ["verdict", "merged_code", "explanation", "warnings"]
            for i, arg in enumerate(args):
                if i < len(field_names):
                    kwargs[field_names[i]] = arg
        super().__init__(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "merged_code": self.merged_code,
            "explanation": self.explanation,
            "warnings": self.warnings,
        }


class TaskDescriptor(BaseModel):
    """What one expert is asked to do."""
    id: str
    agent: str
    domain: str
    prompt: str
    signals: List[MeshSignal] = []


class EnvironmentMetadata(BaseModel):
    language: str
    test_framework: str
    code_tag: str
    extension: str
    assert_examples: str
    architectural_invariant: Optional[str] = None


class DomainConfig(BaseModel):
    name: str
    model: str
    tag: str
    ready: bool = True
    allowed_extensions: Set[str] = Field(default_factory=set)
    ledger_rule: Optional[str] = None
    description: str
    ledger: str
    system_prompt: str


class EcosystemCartridgeContract(BaseModel):
    """Authoritative schema for hot-swappable project cartridges."""
    ecosystem_name: str
    domains: Dict[str, DomainConfig]
    alias_map: Dict[str, str]
    environment_metadata: Dict[str, EnvironmentMetadata]
    acronym_glossary: Dict[str, str] = Field(default_factory=dict)
    procedural_stopwords: Set[str] = Field(default_factory=set)
    unavailable_domains: List[str] = Field(default_factory=list)

    # ── Kernel agnosticism: project-specific rule injection ──────────────────
    # These fields let the cartridge supply content that was previously
    # hardcoded inside the kernel prompt layer (_prompts.py).

    # Set of domain keys whose outputs should pass through the Reasoning Gate.
    # Kernel default is an empty set — the cartridge decides which domains qualify.
    reasoning_gate_domains: Set[str] = Field(default_factory=set)

    # Review checklist injected into REVIEW_PROMPT at assembly time.
    # The kernel provides a universal fallback; the cartridge overrides with
    # project-specific rule-file references and architectural invariants.
    review_prompt_extra: str = ""

    # Project-specific API binding and coding mandates appended to LEDGER_MEMORY_RULE.
    # e.g. "Use sol2 bindings exclusively. Never call raw Lua C API."
    coding_mandates: str = ""

    # Domain-specific terminology note injected into ANALYST_SYSTEM.
    # e.g. "In this project, 'game' means 'Attraction'."
    terminology_note: str = ""

    # Relative path to the architecture / director ledger.
    architecture_ledger: str = "docs/memory/architecture_ledger.md"


class Task:
    """A work item in the mesh queue. Non-Pydantic — plain data class
    for backward compatibility with characterization tests (test_import.py).
    """
    def __init__(self, agent: str, spec: str, parent: str = None,
                 task_id: str = None, is_query: bool = False,
                 iteration: int = 0, context: str = "",
                 depends_on: Optional[List[str]] = None,
                 target_file: Optional[str] = None):
        self.agent = agent
        self.spec = spec
        self.parent = parent
        self.task_id = task_id or f"{agent}_{datetime.now().strftime('%H%M%S%f')}"
        self.is_query = is_query
        self.iteration = iteration
        self.context = context
        self.output = ""
        self.signals = []
        self.double_check = None
        self.completed = False
        self.pinned_blocks: set = set()  # Block IDs pinned to prevent page-out
        self.depends_on: List[str] = depends_on or []
        # target_file: optional relative path hint used by _flush_results_to_workspace
        # to write agent output to disk before compilation. When None the flush is skipped
        # for that task (content is still carried in all_results_dict).
        self.target_file: Optional[str] = target_file
        # ── Directive B: Pro-Mode Inheritance — tracks paged-in content cache ──
        self.paged_files_cache: Dict[str, str] = {}

    def __repr__(self):
        return f"Task({self.agent}, parent={self.parent}, query={self.is_query}, iter={self.iteration})"


class PipelineContext(BaseModel):
    """The single authoritative state bag passed sequentially through all experts.

    This is how the highly interconnected MoE features communicate across file boundaries.
    No async/await — purely synchronous state threading through the entire pipeline.
    """
    model_config = ConfigDict(extra='allow')

    project_root: Path
    memory_dir: Path
    session_id: str
    tasks: List[TaskDescriptor]
    global_signals: List[MeshSignal]
    current_task_index: int = 0
    consensus_results: Dict[str, ConsensusResult] = {}
    ollama_endpoint: str = "http://192.168.0.16:11434"


    # ── Offload Store ────────────────────────────────────────────────────────
    offload_store: Optional[Any] = None

    # ── LRU Doc Cache ────────────────────────────────────────────────────────
    doc_cache: Dict[str, Tuple[str, float]] = {}
    doc_cache_ttl: int = 300
    doc_cache_max: int = 8

    # ── Session Timeline ─────────────────────────────────────────────────────
    session_timeline_path: Optional[Path] = None
    max_output_chars: int = 4000

    # ── Referenced Files Cache ───────────────────────────────────────────────
    referenced_files_cache: str = ""

    # ── Mesh Work Queue API ──────────────────────────────────────────────────
    mesh_task_registry: Dict[str, dict] = {}
    mesh_results: Dict[str, str] = {}
    mesh_work_queue: List[str] = []
    mesh_registry_lock: bool = False

    # ── Progress Listeners ───────────────────────────────────────────────────
    progress_listeners: List[Callable] = []

    # ── Insanity Detector ────────────────────────────────────────────────────
    seen_code_hashes: Set[str] = set()

    # ── File Hash Dictionary for Pre-Merge Hash Locking (Task 2) ─────────────
    file_hashes: Dict[str, str] = {}

    # ── Tribunal / Appellate Court state (Tasks 3-4) ─────────────────────────
    pending_appeals: List[Dict[str, Any]] = []
    tribunal_verdicts: Dict[str, str] = {}

    # ── Runtime accumulators (populated during pipeline execution) ───────────

    all_results: List[Dict[str, Any]] = []
    all_results_dict: Dict[str, str] = {}

    # ── Blueprint cross-iteration carry-forward: snapshots of approved on-disk files ──
    completed_file_snapshots: Dict[str, str] = {}

    all_vetos: List[Dict[str, Any]] = []
    all_objects: List[Dict[str, Any]] = []
    all_recourses: List[Dict[str, Any]] = []
    all_consults: List[Dict[str, Any]] = []
    conflict_resolutions: List[str] = []
    consensus_checks: Dict[str, bool] = {}
    review_output: str = ""
    review_verdict: str = ""
    final_verdict: str = ""
    final_output: str = ""
    user_prompt: str = ""

    # ── Pro Mode ──────────────────────────────────────────────────────────────
    # pro_mode is kept for legacy read compatibility; authoritative state is
    # math_heavy_tasks (per-task set) and pro_mode_always (global override).
    pro_mode: bool = False
    math_heavy_tasks: Set[str] = Field(default_factory=set)
    pro_mode_always: bool = False  # set when user answers "always" at the prompt

    # ── Run-time accumulators (mesh_loops.py) ─────────────────────────────────
    director_output: str = ""

    gdd_context: str = ""
    project_state: str = ""
    interface_manifest: str = ""
    structure: str = ""
    
    # ── Cartridge Ecosystem Topologies ────────────────────────────────────────
    domain_registry: dict = {}
    alias_map: dict = {}
    domain_metadata_registry: dict = {}
    mounted_cartridge: Optional[EcosystemCartridgeContract] = None

    def mount_cartridge(self, cartridge_class: Any) -> None:
        """Dynamically mounts an agent ecosystem cartridge into the orchestrator."""
        self.domain_registry = cartridge_class.get_domain_registry()
        self.alias_map = cartridge_class.get_alias_map()
        self.domain_metadata_registry = cartridge_class.get_environment_metadata()
        # Derive a human-readable ecosystem name from the class if available,
        # falling back to the class's __name__ stripped of common suffixes.
        if hasattr(cartridge_class, "ECOSYSTEM_NAME"):
            self._cartridge_ecosystem_name = cartridge_class.ECOSYSTEM_NAME
        else:
            raw = getattr(cartridge_class, "__name__", "") or ""
            self._cartridge_ecosystem_name = (
                raw.replace("AgentCartridge", "")
                   .replace("Cartridge", "")
                   .strip() or "<project>"
            )
        # Pull the new agnosticism fields if the cartridge exposes them.
        # These are consumed by _prompts.pipeline_bootstrap_prompts().
        if hasattr(cartridge_class, "get_reasoning_gate_domains"):
            self._cartridge_reasoning_gate_domains = cartridge_class.get_reasoning_gate_domains()
        if hasattr(cartridge_class, "get_coding_mandates"):
            self._cartridge_coding_mandates = cartridge_class.get_coding_mandates()
        if hasattr(cartridge_class, "get_review_prompt_extra"):
            self._cartridge_review_prompt_extra = cartridge_class.get_review_prompt_extra()
        if hasattr(cartridge_class, "get_review_system_extra"):
            self._cartridge_review_system_extra = cartridge_class.get_review_system_extra()
        if hasattr(cartridge_class, "get_terminology_note"):
            self._cartridge_terminology_note = cartridge_class.get_terminology_note()
        if hasattr(cartridge_class, "get_project_context"):
            self._cartridge_get_project_context = cartridge_class.get_project_context
        if hasattr(cartridge_class, "get_project_context_scoped"):
            self._cartridge_get_project_context_scoped = cartridge_class.get_project_context_scoped
        if hasattr(cartridge_class, "get_environment_metadata"):
            self._cartridge_environment_metadata = cartridge_class.get_environment_metadata()
        if hasattr(cartridge_class, "get_bridge_contract"):
            self._cartridge_build_bridge_contract = cartridge_class.get_bridge_contract
        if hasattr(cartridge_class, "get_director_extra"):
            self._cartridge_get_director_extra = cartridge_class.get_director_extra

    def mount_ecosystem(self, cartridge: EcosystemCartridgeContract) -> None:
        """Binds a validated ecosystem cartridge directly into the kernel runtime."""
        self.mounted_cartridge = cartridge
        self.domain_registry = {k: dict(v) for k, v in cartridge.domains.items()}
        self.alias_map = dict(cartridge.alias_map)
        self.domain_metadata_registry = {k: dict(v) for k, v in cartridge.environment_metadata.items()}
    active_code_index: str = ""
    conflicts_str: str = ""
    run_id: str = ""
    output_parts: List[str] = []
    processed_ids: Set[str] = set()
    query_results: Dict[str, str] = {}
    pending_queries: Dict[str, Any] = {}
    pending_fetches: Dict[str, Any] = {}
    review_cycle: int = 0
    all_checks_pass: bool = False
    resumed_blocked: bool = False
    snapshot: Optional[Any] = None
    pre_flight_errors: str = ""
    tasks_list: List[Dict[str, Any]] = []
    task_map: Dict[str, Any] = {}
    consensus_iteration: int = 0
    checkpoint_id: str = ""
    all_approvals_dict: Dict[str, bool] = {}
    seen_code_hashes_set: Set[str] = set()
    is_chat: bool = False
    session_mgr: Optional[Any] = None

    # ── Phase I: Core Memory Table (MemGPT Alignment) ──────────────────────
    # Immutable table anchoring core project constants that must survive
    # context pruning. Excluded from character-count evictions by token_budget.py.
    core_memory_table: Dict[str, str] = {}

    # ── Pre-Decomposition Architect Output ───────────────────────────────────
    attraction_design: Optional[AttractionDesign] = None

    # ── Cross-Agent Integration Schema ───────────────────────────────────────
    integration_schema: Optional[IntegrationSchema] = None

    # ── Coverage & Runtime Feedback ──────────────────────────────────────────
    coverage_gaps: List[str] = Field(default_factory=list)
    runtime_errors: List[str] = Field(default_factory=list)

    # ── Phase III: AST Patch Models (LangGraph Alignment) ───────────────────
    # Structured patch sets for state-reducing merge operations.
    pending_patches: List[dict] = []

    # ── Circuit Breaker Retry Counts (Day 4) ──────────────────────────────
    retry_counts: Dict[str, int] = {}

    @property
    def canonical_request(self) -> str:
        """Return the original human feature request, stripping any auto-fed
        ``<execution_environment>`` XML wrapper that blueprint continuation
        replaces ``user_prompt`` with.  Always safe to embed in model prompts."""
        raw = getattr(self, "_original_user_prompt", None) or self.user_prompt
        if not raw:
            return ""
        # Strip the outer <macro_invariants>/<execution_environment> shell that
        # mesh_fetches.py wraps around the original request during continuation.
        import re as _re
        stripped = _re.sub(
            r"<execution_environment>.*?</execution_environment>",
            "", raw, flags=_re.DOTALL,
        )
        stripped = _re.sub(
            r"<macro_invariants>.*?</macro_invariants>",
            "", stripped, flags=_re.DOTALL,
        )
        stripped = stripped.strip()
        return stripped if stripped else raw.strip()

    def load_state(self, state: dict) -> None:
        """Restore runtime accumulators from a saved checkpoint state dict.
        Preserves static configuration fields (project_root, memory_dir, etc.).
        Used for BLOCKED checkpoint resurrection."""
        for key, value in state.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def reset_state(self) -> None:
        """Reset all runtime accumulators to their default values.
        Preserves static configuration fields (project_root, memory_dir, etc.)."""
        self.user_prompt = ""
        self.director_output = ""
        self.gdd_context = ""
        self.project_state = ""
        self.structure = ""
        self.active_code_index = ""
        self.conflicts_str = ""
        self.run_id = ""
        self.interface_manifest = ""
        self.output_parts = []
        self.processed_ids = set()
        self.query_results = {}
        self.pending_queries = {}
        self.pending_fetches = {}
        self.review_cycle = 0
        self.all_checks_pass = False
        self.resumed_blocked = False
        self.snapshot = None
        self.pre_flight_errors = ""
        self.tasks_list = []
        self.task_map = {}
        self.consensus_iteration = 0
        self.checkpoint_id = ""
        self.all_results = []
        self.all_results_dict = {}
        self.all_vetos = []
        self.all_objects = []
        self.all_recourses = []
        self.all_consults = []
        self.conflict_resolutions = []
        self.consensus_checks = {}
        self.review_output = ""
        self.review_verdict = ""
        self.final_verdict = ""
        self.final_output = ""
        self.is_chat = False
        self.session_mgr = None
        self.seen_code_hashes_set = set()
        self.all_approvals_dict = {}
        self.seen_code_hashes = set()
        self.tasks = []
        self.global_signals = []
        self.current_task_index = 0
        self.consensus_results = {}
        self.offload_store = None
        self.doc_cache = {}
        self.session_timeline_path = None
        self.referenced_files_cache = ""
        self.mesh_task_registry = {}
        self.mesh_results = {}
        self.mesh_work_queue = []
        self.mesh_registry_lock = False
        self.pending_patches = []
        self.retry_counts = {}
        self.math_heavy_tasks = set()
        self.attraction_design = None
        self.integration_schema = None
        self.coverage_gaps = []
        self.runtime_errors = []
