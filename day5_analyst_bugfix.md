# Analyst Context Bugfix
*Status: Active*

## Part 1: Fix `_pipeline_helpers.py` (Brittle Regex)
- [x] **Task 1:** Open `_pipeline_helpers.py`. Locate the `get_project_state` function (around line 145).
- [x] **Task 2:** Delete the regex approach for `todo.md`. Instead, read the `todo.md` file and append all lines that DO NOT contain the string `"✅ Done"`. This ensures all incomplete tasks, regardless of table format, are injected into the Analyst's context.

## Part 2: Fix `gdd_extractor.py` (Keyword Mapping)
- [x] **Task 3:** Open `gdd_extractor.py`.
- [x] **Task 4:** Add new keyword mappings to `GDD_SECTION_MAP`. Map "game", "games", and "mini-game" to `["Attraction System", "Attraction Manager"]`. Also ensure "attractions" maps to `["Attraction System"]`.
