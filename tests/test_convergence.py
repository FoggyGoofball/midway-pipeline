"""
test_convergence.py — unit tests for the deterministic convergence trip-wire.
"""

import pytest

from _convergence import error_signature, should_trip_on_stale


class TestErrorSignature:
    def test_line_numbers_are_normalized(self):
        a = error_signature("attractions/strongman/strongman.lua:66: 'end' expected near 'else'")
        b = error_signature("attractions/strongman/strongman.lua:135: 'end' expected near 'else'")
        assert a == b

    def test_prose_line_word_normalized(self):
        a = error_signature("Syntax error at line 52 near 'else'")
        b = error_signature("Syntax error at line 99 near 'else'")
        assert a == b

    def test_whitespace_and_case_normalized(self):
        a = error_signature("  RuntimeSim:   NIL HANDLE   mallet\n")
        b = error_signature("runtimeSim: nil handle mallet")
        assert a == b

    def test_different_defects_differ(self):
        a = error_signature("'end' expected near 'else'")
        b = error_signature("unexpected symbol near ')'")
        assert a != b

    def test_empty_signature(self):
        assert error_signature("") == ""
        assert error_signature("   ") == ""


class TestShouldTripOnStale:
    def test_no_history_does_not_trip(self):
        assert should_trip_on_stale([], "some error at line 10") is False

    def test_first_repeat_trips_at_default_threshold(self):
        history = [error_signature("'end' expected near 'else'")]
        assert should_trip_on_stale(history, "'end' expected near 'else'") is True

    def test_different_signature_does_not_trip(self):
        history = [error_signature("'end' expected near 'else'")]
        assert should_trip_on_stale(history, "unexpected symbol near ')'") is False

    def test_threshold_three_needs_two_prior(self):
        sig = "'end' expected near 'else'"
        history = [error_signature(sig)]
        # threshold 3 -> current is the 2nd occurrence, not yet enough
        assert should_trip_on_stale(history, sig, threshold=3) is False
        history.append(error_signature(sig))
        # now current is the 3rd occurrence
        assert should_trip_on_stale(history, sig, threshold=3) is True
