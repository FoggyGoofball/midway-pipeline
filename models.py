"""
Pydantic models & typed enums — the shared data contract for the entire pipeline.
All state flows through these models. No async/await — purely synchronous.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field


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


class Task:
    """A work item in the mesh queue. Non-Pydantic — plain data class
    for backward compatibility with characterization tests (test_import.py).
    """
    def __init__(self, agent: str, spec: str, parent: str = None,
                 task_id: str = None, is_query: bool = False,
                 iteration: int = 0, context: str = "",
                 depends_on: Optional[List[str]] = None):
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
        # ── Directive B: Pro-Mode Inheritance — tracks paged-in content cache ──
        self.paged_files_cache: Dict[str, str] = {}

    def __repr__(self):
        return f"Task({self.agent}, parent={self.parent}, query={self.is_query}, iter={self.iteration})"


class PipelineContext(BaseModel):
    """The single authoritative state bag passed sequentially through all experts.
    
    This is how the highly interconnected MoE features communicate across file boundaries.
    No async/await — purely synchronous state threading through the entire pipeline.
    """
    project_root: Path
    memory_dir: Path
    session_id: str
    tasks: List[TaskDescriptor]
    global_signals: List[MeshSignal]
    current_task_index: int = 0
    consensus_results: Dict[str, ConsensusResult] = {}
    ollama_endpoint: str = "http://localhost:11434"

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
    all_vetos: List[Dict[str, Any]] = []
    all_objects: List[Dict[str, Any]] = []
    all_approvals: Dict[str, bool] = {}
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
    pro_mode: bool = False

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

    # ── Circuit Breaker Retry Counts (Day 4) ──────────────────────────────
    retry_counts: Dict[str, int] = {}

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
        self.all_approvals = {}
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
