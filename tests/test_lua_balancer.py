"""Tests for the deterministic Lua block balancer (_lua_balancer.py)."""

import pytest

from _lua_balancer import balance_lua_blocks


@pytest.mark.parametrize(
    "src, expected_actions",
    [
        # One stray `end` at the end of a well-formed file.
        (
            "function OnLoad()\n    local x = 1\n    if x > 0 then\n        x = 2\n    end\nend\nend\n",
            ["removed surplus 'end'"],
        ),
        # Missing `end` (both the if and the function are unclosed).
        (
            "function OnLoad()\n    if true then\n        print(1)\n",
            ["appended 2 missing 'end'"],
        ),
        # repeat/until is a balanced pair and must not be touched.
        (
            "local i = 0\nrepeat\n    i = i + 1\nuntil i >= 3\n",
            [],
        ),
    ],
)
def test_balance_lua_blocks(src, expected_actions):
    fixed, actions = balance_lua_blocks(src)
    assert len(actions) == len(expected_actions)
    for action, expected in zip(actions, expected_actions):
        assert action.startswith(expected)
    # Output must never contain conflict markers.
    assert "<<<<<<<" not in fixed and "=======" not in fixed and ">>>>>>>" not in fixed


def test_balanced_file_is_untouched():
    src = (
        "function OnLoad()\n"
        "    MidwayPhysics.OnStep(function(dt)\n"
        "        if dt > 0 then\n"
        "            return 1\n"
        "        end\n"
        "    end)\n"
        "end\n"
    )
    fixed, actions = balance_lua_blocks(src)
    assert fixed == src
    assert actions == []


def test_ignores_end_inside_strings_and_comments():
    src = (
        "-- a comment with the word end inside it\n"
        "local s = \"end and until are not keywords here\"\n"
        "function OnLoad()\n"
        "    local t = [[\n"
        "    end\n"
        "    until\n"
        "    ]]\n"
        "end\n"
    )
    fixed, actions = balance_lua_blocks(src)
    assert fixed == src
    assert actions == []


def test_refuses_conflict_markers():
    src = "function OnLoad()\n<<<<<<< SEARCH\nend\n=======\nend\n>>>>>>> REPLACE\n"
    fixed, actions = balance_lua_blocks(src)
    assert fixed == src
    assert actions == []
