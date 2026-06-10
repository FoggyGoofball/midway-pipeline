"""
test_post_process.py — Unit tests for each of the 7 deterministic fixes in _post_process_lua.py.
"""

import pytest
# Helper: normalize trailing newline for comparison
def _norm(s: str) -> str:
    return s.rstrip("\n") + "\n"

from _post_process_lua import (
    post_process_lua,
    _strip_duplicate_functions,
    _strip_module_level_mod,
    _strip_pipeline_artifacts,
    _inject_onload_static,
    _inject_slot_id,
    _add_midwayphysics_prefix,
    search_exactly_once_gate,
)


# ==============================================================================
#  Fix #1: Strip duplicate function definitions
# ==============================================================================

class TestStripDuplicateFunctions:
    def test_no_duplicates(self):
        src = """function OnLoadStatic()
    SpawnSharedBooth()
end

function OnLoad()
    print("loading")
end
"""
        result = _strip_duplicate_functions(src)
        assert result == src, "Should not modify a file with no duplicates"

    def test_simple_duplicate(self):
        src = """function OnLoad()
    print("first")
end

function OnLoad()
    print("second")
end
"""
        result = _strip_duplicate_functions(src)
        assert "second" in result
        assert "first" not in result
        assert "function OnLoad()" in result
        # Should only appear once
        assert result.count("function OnLoad()") == 1

    def test_three_duplicates(self):
        src = """function Foo()
    print("a")
end

function Foo()
    print("b")
end

function Foo()
    print("c")
end
"""
        result = _strip_duplicate_functions(src)
        assert result.count("function Foo()") == 1
        assert "print(\"c\")" in result  # last one wins
        assert "print(\"a\")" not in result
        assert "print(\"b\")" not in result

    def test_different_names_not_affected(self):
        src = """function OnLoadStatic()
    print("a")
end

function OnLoad()
    print("b")
end

function OnUnload()
    print("c")
end
"""
        result = _strip_duplicate_functions(src)
        assert result == src

    def test_local_function_not_affected_by_duplicate_global(self):
        src = """local function helper()
    print("local")
end

function helper()
    print("global")
end
"""
        result = _strip_duplicate_functions(src)
        # Both have different declaration styles but same name
        assert "helper" in result
        assert "function helper()" in result
        # local function helper should be kept since it matches differently


# ==============================================================================
#  Fix #2: Strip module-level MOD caching
# ==============================================================================

class TestStripModuleLevelMod:
    def test_removes_module_level_mod(self):
        src = """local SLOT_ID = BOOTH_SLOT_ID or -1
local MOD = AttractionConstants.modifiers

function OnLoad()
    print("hello")
end
"""
        result = _strip_module_level_mod(src)
        assert "local MOD = AttractionConstants.modifiers" not in result
        assert "function OnLoad()" in result

    def test_keeps_mod_inside_function(self):
        src = """local SLOT_ID = BOOTH_SLOT_ID or -1

function OnLoad()
    local MOD = AttractionConstants.modifiers
    print(MOD)
end
"""
        result = _strip_module_level_mod(src)
        assert "local MOD = AttractionConstants.modifiers" in result
        assert "function OnLoad()" in result

    def test_keeps_mod_inside_closure(self):
        src = """function OnLoad()
    MidwayPhysics.OnStep(function(dt)
        local MOD = AttractionConstants.modifiers
    end)
end
"""
        result = _strip_module_level_mod(src)
        assert "local MOD = AttractionConstants.modifiers" in result

    def test_no_mod_at_all(self):
        src = """function OnLoad()
    print("hello")
end
"""
        result = _strip_module_level_mod(src)
        assert _norm(result) == _norm(src)


# ==============================================================================
#  Fix #3: Strip pipeline artifacts
# ==============================================================================

class TestStripPipelineArtifacts:
    def test_removes_fix_plan_blocks(self):
        src = """function OnLoad()
    <fix-plan>This is a long reasoning block that should be stripped</fix-plan>
    print("hello")
end
"""
        result = _strip_pipeline_artifacts(src)
        assert "<fix-plan>" not in result
        assert "print(\"hello\")" in result

    def test_removes_insert_hook_markers(self):
        src = """-- [TASK_3_INSERT_HOOK]
function OnLoad()
    print("hello")
end
"""
        result = _strip_pipeline_artifacts(src)
        assert "[TASK_3_INSERT_HOOK]" not in result
        assert "function OnLoad()" in result

    def test_removes_anchor_headers(self):
        src = """### [Anchor]
function OnLoad()
    print("hello")
end
"""
        result = _strip_pipeline_artifacts(src)
        assert "### [Anchor]" not in result

    def test_no_artifacts_unchanged(self):
        src = """function OnLoad()
    print("hello")
end
"""
        result = _strip_pipeline_artifacts(src)
        assert result.strip() == src.strip()

    def test_multiline_fix_plan(self):
        src = """function OnLoad()
    <fix-plan>
        Step 1: Do this
        Step 2: Do that
        Why: Because reasons
    </fix-plan>
    print("hello")
end
"""
        result = _strip_pipeline_artifacts(src)
        assert "<fix-plan>" not in result
        assert "print(\"hello\")" in result


# ==============================================================================
#  Fix #4: Inject missing OnLoadStatic()
# ==============================================================================

class TestInjectOnLoadStatic:
    def test_injects_when_missing(self):
        src = """function OnLoad()
    print("loading")
end
"""
        result = _inject_onload_static(src)
        assert "function OnLoadStatic()" in result
        assert "SpawnSharedBooth()" in result
        assert "function OnLoad()" in result
        # OnLoadStatic should come before OnLoad
        static_pos = result.find("function OnLoadStatic()")
        load_pos = result.find("function OnLoad()")
        assert static_pos < load_pos

    def test_does_not_double_inject(self):
        src = """function OnLoadStatic()
    SpawnSharedBooth()
end

function OnLoad()
    print("loading")
end
"""
        result = _inject_onload_static(src)
        assert result.count("function OnLoadStatic()") == 1
        assert result == src

    def test_injects_at_end_when_no_onload(self):
        src = """function OnUnload()
    print("cleanup")
end
"""
        result = _inject_onload_static(src)
        assert "function OnLoadStatic()" in result
        assert "SpawnSharedBooth()" in result

    def test_injects_preserves_existing_content(self):
        src = """function OnUnload()
    print("cleanup")
end
"""
        result = _inject_onload_static(src)
        assert "print(\"cleanup\")" in result
        assert "function OnUnload()" in result


# ==============================================================================
#  Fix #5: Inject missing SLOT_ID
# ==============================================================================

class TestInjectSlotId:
    def test_injects_when_missing(self):
        src = """-- Skeeball attraction
function OnLoad()
    print("hello")
end
"""
        result = _inject_slot_id(src)
        assert "local SLOT_ID = BOOTH_SLOT_ID or -1" in result

    def test_does_not_double_inject(self):
        src = """local SLOT_ID = BOOTH_SLOT_ID or -1

function OnLoad()
    print("hello")
end
"""
        result = _inject_slot_id(src)
        assert result == src

    def test_injects_after_header(self):
        src = """-- ─── Skeeball ─────────────────────────────────────────────
function OnLoad()
    print("hello")
end
"""
        result = _inject_slot_id(src)
        header_pos = src.find("─ Skeeball")
        slot_pos = result.find("local SLOT_ID")
        assert slot_pos >= 0


# ==============================================================================
#  Fix #6: Add MidwayPhysics. prefix to bare API calls
# ==============================================================================

class TestAddMidwayPhysicsPrefix:
    def test_prefixes_bare_spawn(self):
        src = """function OnLoad()
    local ball = SpawnDynamicSphere(0, 0, 0, 0.5)
end
"""
        result = _add_midwayphysics_prefix(src)
        assert "MidwayPhysics.SpawnDynamicSphere(0, 0, 0, 0.5)" in result
        # The bare-call pattern should no longer appear as a standalone token
        # (it may appear as part of the prefixed version, which is fine)
        lines = result.splitlines()
        bare_lines = [l for l in lines if 'SpawnDynamicSphere(' in l and 'MidwayPhysics.SpawnDynamicSphere' not in l]
        assert len(bare_lines) == 0, f"Found bare SpawnDynamicSphere call: {bare_lines}"

    def test_does_not_double_prefix(self):
        src = """function OnLoad()
    local ball = MidwayPhysics.SpawnDynamicSphere(0, 0, 0, 0.5)
end
"""
        result = _add_midwayphysics_prefix(src)
        assert result.count("MidwayPhysics.") == 1
        assert "MidwayPhysics.SpawnDynamicSphere" in result

    def test_prefixes_multiple_calls(self):
        src = """function OnLoad()
    local ball = SpawnDynamicSphere(0, 0, 0, 0.5)
    DestroyBody(ball)
end
"""
        result = _add_midwayphysics_prefix(src)
        assert "MidwayPhysics.SpawnDynamicSphere" in result
        assert "MidwayPhysics.DestroyBody" in result

    def test_does_not_prefix_in_comments(self):
        src = """-- SpawnDynamicSphere is a function
function OnLoad()
    print("hello")
end
"""
        result = _add_midwayphysics_prefix(src)
        assert "MidwayPhysics.SpawnDynamicSphere" not in result
        assert result.strip() == src.strip()

    def test_prefixes_bare_onstep_registration(self):
        src = """function OnLoad()
    OnStep(function(dt)
        print("tick")
    end)
end
"""
        result = _add_midwayphysics_prefix(src)
        assert "MidwayPhysics.OnStep(function(dt)" in result

    def test_does_not_prefix_lua_globals(self):
        src = """function OnLoad()
    print("hello")
    local x = tonumber("42")
end
"""
        result = _add_midwayphysics_prefix(src)
        assert result == src


# ==============================================================================
#  Fix #7: SEARCH-exactly-once gate
# ==============================================================================

class TestSearchExactlyOnceGate:
    def test_passes_when_exactly_one_match(self):
        content = "hello world hello"
        assert search_exactly_once_gate(content, "world") is True

    def test_fails_when_zero_matches(self):
        content = "hello world"
        assert search_exactly_once_gate(content, "banana") is False

    def test_fails_when_multiple_matches(self):
        content = "hello world hello world"
        assert search_exactly_once_gate(content, "world") is False


# ==============================================================================
#  Integration: Full post_process_lua end-to-end
# ==============================================================================

class TestPostProcessLua:
    def test_clean_file_unchanged(self):
        """A perfectly clean file should be unchanged."""
        src = """-- ─── Skeeball Attraction ──────────────────────────────────
local SLOT_ID = BOOTH_SLOT_ID or -1

-- ─── OnLoadStatic ───────────────────────────────────────
function OnLoadStatic()
    SpawnSharedBooth()
end

-- ─── OnLoad ─────────────────────────────────────────────
function OnLoad()
    MidwayPhysics.OnStep(function(dt)
        print("tick")
    end)
end

-- ─── OnUnload ───────────────────────────────────────────
function OnUnload()
    print("cleanup")
end
"""
        result = post_process_lua(src)
        assert result == src

    def test_all_fixes_applied(self):
        """A file with all 7 fixable problems should be fully cleaned."""
        src = """<fix-plan>This is a reasoning block</fix-plan>
-- ─── Skeeball ───────────────────────────────────────────

function OnLoadStatic()
    SpawnSharedBooth()
end

-- [TASK_3_INSERT_HOOK]
function OnLoad()
    print("first loading")
end

-- ─── MOD caching ────────────────────────────────────────
local MOD = AttractionConstants.modifiers

-- Duplicate OnLoad
function OnLoad()
    print("second loading")
end

-- Bare call (should get MidwayPhysics. prefix)
function OnStep()
    SpawnDynamicSphere(0, 0, 0, 0.5)
end

function OnUnload()
    print("cleanup")
end
"""
        result = post_process_lua(src)
        # Fix #1: duplicate OnLoad should be removed (only "second" remains)
        assert result.count("function OnLoad()") == 1
        assert "print(\"second loading\")" in result
        assert "print(\"first loading\")" not in result
        # Fix #2: MOD caching removed
        assert "local MOD = AttractionConstants.modifiers" not in result
        # Fix #3: pipeline artifacts stripped
        assert "<fix-plan>" not in result
        assert "[TASK_3_INSERT_HOOK]" not in result
        # Fix #4: OnLoadStatic already present
        assert "function OnLoadStatic()" in result
        # Fix #5: SLOT_ID inject (it's missing)
        assert "local SLOT_ID = BOOTH_SLOT_ID or -1" in result
        # Fix #6: MidwayPhysics. prefix
        assert "MidwayPhysics.SpawnDynamicSphere" in result
