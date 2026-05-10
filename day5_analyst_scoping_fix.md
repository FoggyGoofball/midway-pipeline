# Analyst Context Bugfix (Phase 2)
*Status: Complete*

## Part 1: Fix `_prompts.py`
- [x] **Task 1:** Open `_prompts.py`. Locate the `ANALYST_SYSTEM` variable.
- [x] **Task 2:** Add the following strict rule to the `RULES:` section of the prompt:
  `6. STRICT SCOPE MATCHING: If the user asks for a specific category (e.g., 'games', 'attractions', 'shaders'), you MUST actively filter the provided documents. ONLY output items that match that exact category. Completely ignore unrelated TODO items, even if they are marked as unimplemented.`

## Part 2: Fix `_pipeline_helpers.py` (Table Preservation)
- [x] **Task 3:** Open `_pipeline_helpers.py`. Locate the `get_project_state` function.
- [x] **Task 4:** Ensure the `todo.md` parsing logic preserves the markdown headers so the LLM knows which category it is looking at. Ensure the logic looks exactly like this:
  ```python
  todo_path = pr / "docs" / "todo.md"
  if todo_path.is_file():
      try:
          text = todo_path.read_text(encoding="utf-8", errors="replace")
          lines.append("### Project TODO List (Unimplemented)")
          for line in text.splitlines():
              # Keep headers, table formatting, and non-done items
              if "✅ Done" not in line and line.strip():
                  lines.append(line)
          lines.append("")
      except Exception:
          pass
