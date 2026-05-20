"""
Steps 2.3, 2.10: Signal parsing + consensus characterization tests.
"""

import pytest
from pipeline import extract_signals, extract_double_check, get_verdict, parse_signal, SignalType, MeshSignal, ConsensusResult


class TestSignalParsing:
    """Lock in extract_signals behavior from lines 2127-2144."""

    def test_extract_approve_signal(self):
        """APPROVE pattern is just [APPROVE] with no colon-args."""
        text = "Looks good. [APPROVE]"
        signals = extract_signals(text)
        assert len(signals) >= 1
        assert signals[0]["type"] == "APPROVE"

    def test_extract_delegate_signal(self):
        text = "[DELEGATE:Lua:C++:Rewrite the physics solver]"
        signals = extract_signals(text)
        assert len(signals) >= 1
        assert signals[0]["type"] == "DELEGATE"

    def test_extract_veto_signal(self):
        """VETO format: [VETO:target:content]."""
        text = "[VETO:agent_cpp:This approach will break the renderer]"
        signals = extract_signals(text)
        assert len(signals) >= 1
        assert signals[0]["type"] == "VETO"

    def test_extract_fetch_signal_returns_empty(self):
        """FETCH is deprecated — superseded by PagingKernel <invoke_kernel> schema.
        Legacy FETCH pattern key is removed from SIGNAL_PATTERNS.
        """
        text = "[FETCH:docs/memory/C++_ledger.md#PhysicsSystem]"
        signals = extract_signals(text)
        assert len(signals) == 0  # No longer matched

    def test_extract_multiple_signals(self):
        text = "[APPROVE][VETO:target:no][DELEGATE:C++:Lua:task]"
        signals = extract_signals(text)
        assert len(signals) >= 3

    def test_no_signals(self):
        text = "This is plain text with no signals."
        signals = extract_signals(text)
        assert len(signals) == 0

    def test_extract_double_check_present(self):
        """DOUBLE_CHECK pattern is a Markdown heading section, not [[DOUBLE_CHECK]]."""
        text = """## Double-Check
**Original prompt:** Write physics code
**My output addresses:** Jolt integration
**Unresolved items:** None
"""
        result = extract_double_check(text)
        assert result is not None
        assert "Write physics code" in result.get("original_prompt", "")

    def test_extract_double_check_absent(self):
        text = "No double check here."
        result = extract_double_check(text)
        assert result is None

    def test_get_verdict_pass_bold(self):
        """get_verdict checks for **PASS** or bare PASS line."""
        assert get_verdict("**PASS**") == "PASS"

    def test_get_verdict_pass_bare(self):
        assert get_verdict("PASS") == "PASS"

    def test_get_verdict_fail_bold(self):
        assert get_verdict("**FAIL**") == "FAIL"

    def test_get_verdict_unknown(self):
        assert get_verdict("bogus text") == "UNKNOWN"


class TestConsensus:
    """Lock in consensus behavior from lines 4042-4118, 4100-4118."""

    def test_signal_type_enum_values(self):
        expected = ["QUERY", "DELEGATE", "RESULT", "APPROVE", "REVISE", "VETO", "OBJECT", "RECOURSE", "CONSULT"]
        for name in expected:
            assert hasattr(SignalType, name)

    def test_mesh_signal_creation(self):
        sig = MeshSignal(SignalType.APPROVE, "agent_cpp", "Looks good", "agent_lua")
        assert sig.type == SignalType.APPROVE
        assert sig.target == "agent_cpp"
        assert sig.content == "Looks good"

    def test_mesh_signal_to_dict(self):
        sig = MeshSignal(SignalType.VETO, "agent_lua", "Bad idea", "agent_cpp")
        d = sig.to_dict()
        assert d["type"] == "VETO"
        assert d["target"] == "agent_lua"

    def test_mesh_signal_repr(self):
        sig = MeshSignal(SignalType.QUERY, "librarian", "What files?", "director")
        r = repr(sig)
        assert "QUERY" in r

    def test_consensus_result_creation(self):
        cr = ConsensusResult("APPROVED", "merged code content", "Looks good", [])
        assert cr.verdict == "APPROVED"
        assert cr.merged_code == "merged code content"
        assert cr.explanation == "Looks good"
        assert cr.warnings == []

    def test_consensus_result_to_dict(self):
        cr = ConsensusResult("CONFLICT", "", "Needs review", ["warning 1"])
        d = cr.to_dict()
        assert d["verdict"] == "CONFLICT"
        assert d["warnings"] == ["warning 1"]

    def test_parse_signal_empty(self):
        result = parse_signal("No signals", source="agent_lua")
        assert result == []

    def test_parse_signal_with_content(self):
        result = parse_signal("[APPROVE]", source="agent_lua")
        assert len(result) >= 1
        if isinstance(result[0], MeshSignal):
            assert result[0].type == SignalType.APPROVE
