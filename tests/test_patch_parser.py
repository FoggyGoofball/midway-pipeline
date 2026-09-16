"""Tests for the patch parser's display-artifact defenses (line-number gutter)."""

from _helpers_exec import _strip_line_number_gutter, _extract_search_replace_blocks


def test_strip_line_number_gutter():
    src = (
        "  37 | -- ALL static geometry\n"
        "  38 | function OnLoadStatic()\n"
        "  40 >     -- hook\n"
        "  41 | end"
    )
    fixed = _strip_line_number_gutter(src)
    assert fixed == (
        "-- ALL static geometry\n"
        "function OnLoadStatic()\n"
        "    -- hook\n"
        "end"
    )


def test_does_not_strip_legit_code():
    src = "local x = 1\nprint('3 | 4')\nend"
    assert _strip_line_number_gutter(src) == src


def test_search_replace_blocks_are_deguttered():
    block = (
        "<<<<<<< SEARCH\n"
        "function OnLoadStatic()\n"
        "end\n"
        "=======\n"
        "  37 | -- ALL static\n"
        "  38 | function OnLoadStatic()\n"
        "  40 >   SpawnSharedBooth()\n"
        "  41 | end\n"
        ">>>>>>> REPLACE\n"
    )
    parsed = _extract_search_replace_blocks(block)
    assert len(parsed) == 1
    assert "|" not in parsed[0]["replace"]
    assert parsed[0]["replace"].strip() == (
        "-- ALL static\n"
        "function OnLoadStatic()\n"
        "  SpawnSharedBooth()\n"
        "end"
    )
