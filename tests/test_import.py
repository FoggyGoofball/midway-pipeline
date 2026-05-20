"""
Step 2.1: Import verification — ensure pipeline.py loads without errors.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_import_pipeline_succeeds():
    """Ensure pipeline.py can be imported as a module without ImportError."""
    import pipeline
    assert hasattr(pipeline, "run_pipeline")
    assert hasattr(pipeline, "run_mesh_pipeline")
    assert hasattr(pipeline, "call_ollama")
    assert hasattr(pipeline, "OffloadStore")
    assert hasattr(pipeline, "Task")
    assert hasattr(pipeline, "TagSuggester")
    assert hasattr(pipeline, "TokenBudget")
    assert hasattr(pipeline, "SignalType")
    assert hasattr(pipeline, "MeshSignal")
    assert hasattr(pipeline, "ConsensusResult")


def test_constants_exist():
    """Verify key configuration constants are present."""
    import pipeline
    assert pipeline.OLLAMA_HOST == "http://192.168.0.16:11434"
    assert pipeline.CODER_MODEL == "qwen2.5-coder:7b"
    # PROJECT_ROOT is set at import time from where pipeline.py lives
    assert isinstance(pipeline.PROJECT_ROOT, Path)
    assert pipeline.MAX_ITERATIONS == 3
    assert pipeline.MAX_CONSENSUS_ITERATIONS == 3
    assert pipeline.OLLAMA_NUM_CTX == 32768
    assert pipeline.MAX_TOKENS == 12000


def test_signal_patterns_exist():
    """Verify all signal pattern keys are present (legacy FETCH superseded by PagingKernel)."""
    import pipeline
    # Keys observed in the actual pipeline.py SIGNAL_PATTERNS dict.
    # NOTE: FETCH, READ_OFFLOADED, MATH_EVAL are PURGED — superseded by
    # the <invoke_kernel> XML schema in the PagingKernel. Legacy handlers
    # are commented out in signals.py but the keys remain in SIGNAL_PATTERNS
    # for APPEAL, MERGE, REJECT, FLUSH, REQUEST_API.
    expected_keys = [
        "APPEAL", "APPROVE", "CONSULT", "DELEGATE", "FLUSH",
        "MERGE", "OBJECT", "QUERY", "RECOURSE", "REJECT",
        "REQUEST_API", "RESULT", "REVISE", "VETO",
    ]
    for key in expected_keys:
        assert key in pipeline.SIGNAL_PATTERNS, f"Missing SIGNAL_PATTERNS key: {key}"
