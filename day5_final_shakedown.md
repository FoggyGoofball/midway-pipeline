# Day 5 Finalization & Lexicon Patch
*Status: Active*

## Part 1: The `gdd_extractor.py` Force-Patch
- [x] **Task 1:** Create a temporary Python script named `patch_gdd_lexicon.py` in the root directory.
- [x] **Task 2:** Write logic in `patch_gdd_lexicon.py` that reads `gdd_extractor.py`. Locate the `GDD_SECTION_MAP` dictionary definition and use `re.sub` or AST manipulation to update it. Specifically, ensure the "attraction", "game", and "mini-game" keys map EXACTLY to the true GDD headers.
- [x] **Task 3:** Execute `patch_gdd_lexicon.py`. Read `gdd_extractor.py` to verify the dictionary was successfully modified. Once verified, delete `patch_gdd_lexicon.py`.

## Part 2: The Omni-Batch Shakedown Command
- [x] **Task 4:** Inform the user that the pipeline is unblocked and ready for the final Day 5 test. Output the exact instruction block for the user to copy/paste into the Stream Server prompt.
