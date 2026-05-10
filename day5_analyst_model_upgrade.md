# Analyst 14B Model Upgrade
*Status: Completed*

## Part 1: Define `ANALYST_MODEL` 
- [x] **Task 1:** Opened `pipeline.py`. Located the Configuration section (around lines 112-120).
- [x] **Task 2:** Added `ANALYST_MODEL = REVIEWER_MODEL` alongside `CHAT_MODEL` and `EXECUTION_MODEL`.

## Part 2: Update the Analyst Invocation
- [x] **Task 3:** In `pipeline.py`, located the `INFORMATIONAL` route block where the Analyst is called (line 391).
- [x] **Task 4:** Changed `call_ollama` invocation to pass `ANALYST_MODEL` instead of `CHAT_MODEL`.
