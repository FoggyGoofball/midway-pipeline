#!/usr/bin/env python3
"""
structured_schemas.py — Rigid Pydantic output contracts for the LLM pipeline.
================================================================================

ARCHITECTURAL STANDARD #1 (Output Validation):
    Every structured LLM output is defined here as a Pydantic ``BaseModel`` with
    explicit type hints.  Regex / ``str.split`` / hand-rolled JSON repair parsers
    are deprecated.  These models are the single source of truth consumed by:

        * ``structured_client.py``  — instructor-wrapped extraction + tenacity retry
        * ``serve_xgrammar.py``     — xgrammar-constrained vLLM/SGLang inference

    Each model carries an idiomatic ``to_*`` bridge method so it can be dropped
    into the legacy ``models.py`` objects without touching downstream code.

Pydantic v2 is required (matching the existing ``models.py`` usage).
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
#  Shared enums — closed vocabularies the LLM MUST pick from.
# ---------------------------------------------------------------------------

class Lifecycle(str, Enum):
    ON_LOAD_STATIC = "OnLoadStatic"
    ON_LOAD = "OnLoad"
    ON_STEP = "OnStep"
    ON_UNLOAD = "OnUnload"
    RUNTIME = "runtime"


class LuaType(str, Enum):
    USERDATA = "userdata"
    NUMBER = "number"
    BOOLEAN = "boolean"
    TABLE = "table"


class Verdict(str, Enum):
    CONFIRMED = "CONFIRMED"
    REVISED = "REVISED"
    REJECTED = "REJECTED"


class Intent(str, Enum):
    """Closed set for the intent classifier (replaces loose string matching)."""
    FEATURE_REQUEST = "FEATURE_REQUEST"
    BUG_FIX = "BUG_FIX"
    REFACTOR = "REFACTOR"
    QUESTION = "QUESTION"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
#  Standard 1: Architect design document (Phase 0).
# ---------------------------------------------------------------------------

class HandleSpec(BaseModel):
    """A physics/object handle the LLM declares for an attraction."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="e.g. 'hBall'")
    lua_type: LuaType = LuaType.USERDATA
    owner_hint: str = Field("", description="short domain label e.g. 'physics' | 'economy'")
    lifecycle: Lifecycle = Lifecycle.ON_LOAD
    description: str = ""


class EventEdgeSpec(BaseModel):
    """A directed event flow edge: trigger -> action."""
    model_config = ConfigDict(extra="forbid")

    trigger: str = Field(..., min_length=1, description="a Lua condition expression")
    action: str = Field(..., min_length=1, description="what happens (domain:call syntax)")


class TaskAnchorSpec(BaseModel):
    """Deterministic anchor comment for downstream SEARCH targets."""
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., description="string task id e.g. '3'")
    hook: str = Field(..., description="literal anchor comment e.g. '-- [TASK_3_INSERT_HOOK] -- physics'")
    location: str = Field("", description="e.g. 'inside OnLoad()', 'at module root'")


class AttractionDesignOutput(BaseModel):
    """Strict schema for the Architect pass (replaces _extract_json / _repair_json)."""
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    summary: str = ""
    handles: List[HandleSpec] = Field(default_factory=list)
    lifecycle_order: List[str] = Field(default_factory=list)
    event_flow: List[EventEdgeSpec] = Field(default_factory=list)
    pool_requirements: dict[str, int] = Field(default_factory=dict)
    economy_hooks: List[str] = Field(default_factory=list)
    feature_checklist: List[str] = Field(default_factory=list)
    task_anchors: List[TaskAnchorSpec] = Field(default_factory=list)

    @field_validator("pool_requirements")
    @classmethod
    def _coerce_pool_counts(cls, v: dict) -> dict:
        """Force every pool count to an int (LLM may emit floats / numeric strings)."""
        return {str(k): int(val) for k, val in v.items()}

    def to_attraction_design(self) -> "AttractionDesign":  # noqa: F821 (resolved lazily)
        """Bridge into the legacy ``models.AttractionDesign`` object."""
        from models import AttractionDesign, HandleDeclaration, EventEdge

        return AttractionDesign(
            title=self.title,
            summary=self.summary,
            handles=[
                HandleDeclaration(
                    name=h.name,
                    lua_type=h.lua_type.value,
                    owner_task=h.owner_hint,
                    lifecycle=h.lifecycle.value,
                    description=h.description,
                )
                for h in self.handles
            ],
            lifecycle_order=self.lifecycle_order,
            event_flow=[
                EventEdge(trigger=e.trigger, action=e.action) for e in self.event_flow
            ],
            pool_requirements=self.pool_requirements,
            economy_hooks=self.economy_hooks,
            feature_checklist=self.feature_checklist,
            task_anchors=[a.model_dump() for a in self.task_anchors],
            raw_json=self.model_dump_json(),
        )


# ---------------------------------------------------------------------------
#  Standard 1: Director task decomposition (the worst regex offender).
# ---------------------------------------------------------------------------

class DirectorTask(BaseModel):
    """A single decomposed task — replaces the brittle `### Task N:` regex grammar."""
    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., ge=1, description="numeric task id")
    domain: str = Field(..., min_length=1, description="domain tag WITHOUT brackets, e.g. 'Lua'")
    title: str = Field(..., min_length=1)
    depends_on: List[int] = Field(default_factory=list, description="task ids this task depends on")
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    hooks: List[Lifecycle] = Field(default_factory=list, description="lifecycle hooks this task registers")
    file: Optional[str] = Field(None, description="relative target file path or None")
    math_heavy: bool = Field(False, description="requires 3D math / physics vector work")

    @field_validator("domain")
    @classmethod
    def _strip_brackets(cls, v: str) -> str:
        """Tolerate an accidental '[Lua]' so a stray bracket never crashes extraction."""
        return v.strip().strip("[]").strip()


class TaskDecomposition(BaseModel):
    """Full Director output: an ordered task array, nothing else."""
    model_config = ConfigDict(extra="forbid")

    tasks: List[DirectorTask] = Field(..., min_length=1, max_length=5)

    def to_markdown(self) -> str:
        """Render the legacy prompt grammar for any consumer that still wants text."""
        lines: list[str] = []
        for t in self.tasks:
            deps = ",".join(str(d) for d in t.depends_on) or "None"
            lines.append(f"### Task {t.id}: [{t.domain}] - {t.title} (DependsOn: {deps})")
            lines.append(f"Inputs: {', '.join(t.inputs) or 'None'}")
            lines.append(f"Outputs: {', '.join(t.outputs) or 'None'}")
            lines.append(
                "Hooks: " + (", ".join(h.value for h in t.hooks) or "None")
            )
            lines.append(f"File: {t.file or 'None'}")
            if t.math_heavy:
                lines.append("[MATH_HEAVY]")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
#  Standard 1: Reviewer verdict (replaces 'start with CONFIRMED/REVISED' parsing).
# ---------------------------------------------------------------------------

class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str = Field("warning", pattern="^(error|warning|info)$")
    location: str = ""
    message: str = Field(..., min_length=1)


class ReviewVerdict(BaseModel):
    """Integration Reviewer output — PASS/FAIL plus concrete issues."""
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict = Verdict.CONFIRMED
    issues: List[ReviewIssue] = Field(default_factory=list)
    corrected_code: Optional[str] = Field(None, description="present when verdict is REVISED")

    @property
    def passed(self) -> bool:
        return self.verdict == Verdict.CONFIRMED


# ---------------------------------------------------------------------------
#  Standard 1: Code patch (SEARCH/REPLACE) — replaces regex block splitting.
# ---------------------------------------------------------------------------

class SearchReplacePatch(BaseModel):
    """A single deterministic SEARCH/REPLACE edit block."""
    model_config = ConfigDict(extra="forbid")

    search: str = Field(..., min_length=1, description="exact text to locate (must be unique)")
    replace: str = Field("", description="replacement text (may be empty for deletion)")


class PatchSet(BaseModel):
    """Collection of non-overlapping edits applied atomically."""
    model_config = ConfigDict(extra="forbid")

    patches: List[SearchReplacePatch] = Field(default_factory=list)
    rationale: str = ""


# ---------------------------------------------------------------------------
#  Standard 1: Syntax gate diagnostic.
# ---------------------------------------------------------------------------

class SyntaxDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int = Field(0, ge=0)
    column: int = Field(0, ge=0)
    message: str = Field(..., min_length=1)
    severity: str = Field("error", pattern="^(error|warning)$")


class SyntaxCheckResult(BaseModel):
    """Syntax-gate output: valid flag plus machine-readable diagnostics."""
    model_config = ConfigDict(extra="forbid")

    valid: bool = Field(...)
    diagnostics: List[SyntaxDiagnostic] = Field(default_factory=list)


# ---------------------------------------------------------------------------
#  Standard 1: Intent classification.
# ---------------------------------------------------------------------------

class IntentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent = Intent.UNKNOWN
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    summary: str = ""


# ---------------------------------------------------------------------------
#  Registry — every schema discoverable by serve_xgrammar.py and tests.
# ---------------------------------------------------------------------------

SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "attraction_design": AttractionDesignOutput,
    "task_decomposition": TaskDecomposition,
    "review_verdict": ReviewVerdict,
    "patch_set": PatchSet,
    "syntax_check": SyntaxCheckResult,
    "intent_classification": IntentClassification,
}


def get_schema(name: str) -> type[BaseModel]:
    """Look up a schema by registry key (raises KeyError on unknown)."""
    if name not in SCHEMA_REGISTRY:
        raise KeyError(f"Unknown schema '{name}'. Available: {sorted(SCHEMA_REGISTRY)}")
    return SCHEMA_REGISTRY[name]


if __name__ == "__main__":
    # Smoke test: schema round-trip + JSON-schema emission.
    sample = {
        "tasks": [
            {
                "id": 1,
                "domain": "Lua",
                "title": "Skeeball ramp physics",
                "depends_on": [],
                "inputs": ["hBall"],
                "outputs": ["hRamp"],
                "hooks": ["OnLoadStatic", "OnStep"],
                "file": "attractions/skeeball.lua",
                "math_heavy": True,
            }
        ]
    }
    parsed = TaskDecomposition.model_validate(sample)
    assert parsed.tasks[0].domain == "Lua"
    assert parsed.to_markdown().startswith("### Task 1: [Lua] -")
    print(parsed.to_markdown())
    print("\nJSON schema keys:", sorted(TaskDecomposition.model_json_schema().keys()))
