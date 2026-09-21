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
    _repair_lua_structure,
    _repair_orphaned_then,
    _neutralize_roblox,
    repair_lua_syntax,
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

    def test_prefixes_bare_midwayinput(self):
        src = """function OnLoad()
    MidwayPhysics.OnStep(function(dt)
        if IsActionDown("fire") then
            print("swing")
        end
        if IsKeyDown("space") then
            print("jump")
        end
    end)
end
"""
        result = _add_midwayphysics_prefix(src)
        assert "MidwayInput.IsActionDown(\"fire\")" in result
        assert "MidwayInput.IsKeyDown(\"space\")" in result

    def test_does_not_double_prefix_midwayinput(self):
        src = """function OnLoad()
    MidwayPhysics.OnStep(function(dt)
        MidwayInput.IsActionDown("fire")
    end)
end
"""
        result = _add_midwayphysics_prefix(src)
        assert result.count("MidwayInput.IsActionDown") == 1

    def test_prefixes_bare_engine_economy(self):
        src = """function OnStep(dt)
    AwardTickets(1)
    AwardTokens(2)
    local s = GetStreak()
end
"""
        result = _add_midwayphysics_prefix(src)
        assert "Engine.AwardTickets(1)" in result
        assert "Engine.AwardTokens(2)" in result
        assert "Engine.GetStreak()" in result


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


# ==============================================================================
#  Fix #32: Hoist cross-lifecycle locals to module scope
# ==============================================================================

class TestHoistCrossLifecycleLocals:
    def test_hoists_onload_local_used_in_onunload(self):
        src = """local SLOT_ID = BOOTH_SLOT_ID or -1

function OnLoad()
    local mallet = MidwayPhysics.SpawnDynamicBox(0, 0, 0, 1, 1, 1, 5)
    local puck = MidwayPhysics.SpawnDynamicSphere(0, 0, 2, 0.2, 1)
end

function OnUnload()
    MidwayPhysics.DestroyBody(mallet)
end
"""
        result = post_process_lua(src)
        # module-level declaration added
        assert "local mallet  -- module-level (shared across lifecycle)" in result
        # `local` stripped inside OnLoad for the hoisted name
        assert "local mallet = MidwayPhysics" not in result
        assert "mallet = MidwayPhysics.SpawnDynamicBox" in result
        # puck is NOT referenced in OnUnload -> stays local inside OnLoad
        assert "local puck = MidwayPhysics.SpawnDynamicSphere" in result

    def test_already_module_level_not_duplicated(self):
        src = """local SLOT_ID = BOOTH_SLOT_ID or -1
local mallet

function OnLoad()
    local mallet = MidwayPhysics.SpawnDynamicBox(0, 0, 0, 1, 1, 1, 5)
end

function OnUnload()
    MidwayPhysics.DestroyBody(mallet)
end
"""
        result = post_process_lua(src)
        # the pre-existing module-level declaration is NOT duplicated
        assert result.count("local mallet") == 1
        assert "local mallet\n" in result
        assert "mallet = MidwayPhysics.SpawnDynamicBox" in result

    def test_no_cleanup_reference_no_change(self):
        src = """local SLOT_ID = BOOTH_SLOT_ID or -1

function OnLoad()
    local mallet = MidwayPhysics.SpawnDynamicBox(0, 0, 0, 1, 1, 1, 5)
end

function OnUnload()
    print("cleanup")
end
"""
        result = post_process_lua(src)
        assert "local mallet = MidwayPhysics.SpawnDynamicBox" in result
        assert "local mallet  -- module-level" not in result


# ==============================================================================
#  Fix #9 extension: bare `mods.*` modifier alias -> MOD.*
# ==============================================================================

class TestModsAlias:
    def test_mods_dot_and_index_become_mod(self):
        src = """local SLOT_ID = BOOTH_SLOT_ID or -1

function OnLoad()
    MidwayPhysics.OnStep(function(dt)
        local f = mods.heat
        local g = mods["sleight_of_hand"]
        local h = mods.unknown_key
    end)
end
"""
        result = post_process_lua(src)
        assert "MOD.heat" in result
        assert "MOD.sleight_of_hand" in result
        # unknown key neutralized to 1.0; no `mods` reference survives
        assert "mods" not in result

    def test_legit_mod_untouched(self):
        src = """local SLOT_ID = BOOTH_SLOT_ID or -1

function OnLoad()
    MidwayPhysics.OnStep(function(dt)
        local MOD = AttractionConstants.modifiers
        local f = MOD.friction
    end)
end
"""
        result = post_process_lua(src)
        assert "MOD.friction" in result


# ==============================================================================
#  Fix #27: Structural stack-scan repair (unbalanced brackets/blocks)
# ==============================================================================

class TestRepairLuaStructure:
    def test_idempotent_on_clean_file(self):
        src = "function OnLoad()\n    print(1)\nend\n\nfunction OnUnload()\nend\n"
        assert _repair_lua_structure(src) == src

    def test_closes_unclosed_table_call(self):
        """CreatePool(..., { ...  never closed -> insert `})` + enclosing `end`."""
        src = (
            'function OnLoad()\n'
            '    MidwayPhysics.CreatePool("puck_pool", 2, 2, {\n'
            '        radius = 0.3\n'
            '\n'
            'function OnStep(dt)\n'
            '    print(dt)\n'
            'end\n'
        )
        out = _repair_lua_structure(src)
        assert '}\n)\nend\nfunction OnStep' in out
        # no phantom stray text: the inserted closers appear exactly once
        assert out.count('}\n)\nend\n') == 1

    def test_closes_anonymous_function_callback(self):
        """OnStep(function(dt) ... end with no `)` and no OnLoad `end`."""
        src = (
            'function OnLoad()\n'
            '    MidwayPhysics.OnStep(function(dt)\n'
            '        print(dt)\n'
            '    end\n'
            '\n'
            'function OnUnload()\n'
            'end\n'
        )
        out = _repair_lua_structure(src)
        assert ')\nend\nfunction OnUnload' in out
        # OnUnload must end up at module level (OnLoad closed before it)
        assert out.index('function OnUnload') > out.index(')\nend\n')

    def test_removes_surplus_end(self):
        src = "function OnLoad()\n    print(1)\nend\nend\n"
        out = _repair_lua_structure(src)
        # surplus `end` is blanked, leaving exactly one live `end`
        assert out.count('end') == 1

    def test_closes_paren_before_truncated_local(self):
        """SpawnSensorBox( unclosed, then a cut-off `local base_` statement."""
        src = (
            'function OnLoad()\n'
            '    local base_wobble_sensor = MidwayPhysics.SpawnSensorBox(\n'
            '        0.0, 0.0, 0.0, 1.0, 0.1, 1.0\n'
            '    local base_\n'
            '    if base_wobble_sensor then\n'
            '        print("ok")\n'
            '    end\n'
            'end\n'
        )
        out = _repair_lua_structure(src)
        assert ')\nlocal base_' in out

    def test_closes_paren_before_statement_if(self):
        """Unclosed call then an `if` statement (not a function declaration)."""
        src = (
            'function OnLoad()\n'
            '    local x = MidwayPhysics.SpawnSensorBox(\n'
            '        0.0, 0.0, 0.0, 1.0\n'
            '    if x then\n'
            '        print("ok")\n'
            '    end\n'
            'end\n'
        )
        out = _repair_lua_structure(src)
        assert ')\nif x then' in out

    def test_repair_lua_syntax_fixes_broken_file(self):
        """Minimal pass must leave the file with no unbalanced closer."""
        src = (
            'function OnLoad()\n'
            '    MidwayPhysics.OnStep(function(dt)\n'
            '        print(dt)\n'
            '    end\n'
            '\n'
            'function OnUnload()\n'
            'end\n'
        )
        out = repair_lua_syntax(src)
        assert ')\nend\nfunction OnUnload' in out


# ==============================================================================
#  Fix #28: orphaned `then`/`do` continuation repair
# ==============================================================================

class TestRepairOrphanedThen:
    def test_comments_orphaned_then_after_phantom_marker(self):
        src = (
            '-- [PHANTOM COLON-CALL] if player:GetAttribute("CanSwing") and\n'
            '-- [PHANTOM COLON-CALL] player:GetAttribute("IsSwinging") and\n'
            '       vector3magnitude(player.Input.KeyCode(Enum.KeyCode.E)) > 0.5 then\n'
            '    isSwinging = true\n'
            'end\n'
        )
        out = _repair_orphaned_then(src)
        assert '-- [orphaned then/do removed]' in out
        assert 'vector3magnitude' in out  # the text is preserved, just commented

    def test_keeps_legit_if_then_after_normal_comment(self):
        src = '-- a normal comment\nif x > 0 then\n    print(x)\nend\n'
        out = _repair_orphaned_then(src)
        assert '-- [orphaned then/do removed]' not in out
        assert 'if x > 0 then' in out

    def test_keeps_multiline_if_continuation(self):
        src = 'if a and\n    b then\n    print(1)\nend\n'
        out = _repair_orphaned_then(src)
        assert '-- [orphaned then/do removed]' not in out

    def test_keeps_for_do(self):
        src = 'for i = 1, 10 do\n    print(i)\nend\n'
        out = _repair_orphaned_then(src)
        assert '-- [orphaned then/do removed]' not in out


# ==============================================================================
#  Fix #29: Roblox/Luau idiom neutralization
# ==============================================================================

class TestNeutralizeRoblox:
    def test_comments_vector3_statement(self):
        src = 'local v = Vector3.new(0, 0, 0)\n'
        out = _neutralize_roblox(src)
        assert '-- [roblox removed]' in out

    def test_comments_position_property(self):
        src = 'local dir = (target.Position - rightHand.Position).Unit\n'
        out = _neutralize_roblox(src)
        assert '-- [roblox removed]' in out

    def test_comments_enum(self):
        src = 'local key = Enum.KeyCode.E\n'
        out = _neutralize_roblox(src)
        assert '-- [roblox removed]' in out

    def test_keeps_legit_physics_call(self):
        src = '    MidwayPhysics.GetPosition(puck)\n'
        out = _neutralize_roblox(src)
        assert '-- [roblox removed]' not in out
        assert 'MidwayPhysics.GetPosition' in out

    def test_keeps_lowercase_position_var(self):
        src = '    local position = MidwayPhysics.GetPosition(puck)\n'
        out = _neutralize_roblox(src)
        assert '-- [roblox removed]' not in out
