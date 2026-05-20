import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class FileASTEntry:
    """AST-level index entry for a single source file.

    Captured deterministically via regex — not a full parser, but sufficient
    for downstream agents to locate symbols and target PAGE_IN precisely.
    """
    rel_path: str
    line_count: int
    classes: List[str] = field(default_factory=list)
    # C++ free functions / Lua global functions visible at file scope
    functions: List[str] = field(default_factory=list)
    # Top-level Lua module tables (e.g. `MyModule = {}`)
    lua_modules: List[str] = field(default_factory=list)
    is_large: bool = False   # True when line_count > LARGE_FILE_THRESHOLD

    def page_in_hint(self) -> str:
        """Return a compact PAGE_IN hint string for the agent's context."""
        parts = [f"`{self.rel_path}` ({self.line_count} lines"]
        if self.is_large:
            parts[-1] += ", LARGE — use targeted PAGE_IN"
        parts[-1] += ")"
        if self.classes:
            parts.append(f"classes: {', '.join(self.classes[:8])}"
                         + (" ..." if len(self.classes) > 8 else ""))
        if self.functions:
            parts.append(f"functions: {', '.join(self.functions[:8])}"
                         + (" ..." if len(self.functions) > 8 else ""))
        if self.lua_modules:
            parts.append(f"modules: {', '.join(self.lua_modules[:6])}")
        return " | ".join(parts)


class ProjectTopology:
    def __init__(self) -> None:
        self.classes: Set[str] = set()
        self.uninstrumented_files: List[str] = []
        # Per-file AST index keyed by relative path
        self.file_index: Dict[str, FileASTEntry] = {}

    def format_ast_summary(self, max_entries: int = 80) -> str:
        """Return a compact, agent-readable AST index suitable for injection
        into a blueprint prompt without blowing the context budget.

        Large files (is_large=True) are listed first so the agent knows
        which files require targeted PAGE_IN rather than full reads.
        """
        if not self.file_index:
            return "(no files indexed)"
        entries = sorted(
            self.file_index.values(),
            key=lambda e: (not e.is_large, e.rel_path),
        )[:max_entries]
        lines = ["## Workspace AST Index\n"]
        large = [e for e in entries if e.is_large]
        small = [e for e in entries if not e.is_large]
        if large:
            lines.append("### Large Files (targeted PAGE_IN required)\n")
            for e in large:
                lines.append(f"- {e.page_in_hint()}")
        if small:
            lines.append("\n### Standard Files\n")
            for e in small:
                lines.append(f"- {e.page_in_hint()}")
        if len(self.file_index) > max_entries:
            lines.append(
                f"\n... ({len(self.file_index) - max_entries} more files not shown)"
            )
        return "\n".join(lines)


# Files with more lines than this threshold are flagged as large and
# downstream agents are told to use targeted PAGE_IN (<lines> or <search>).
LARGE_FILE_THRESHOLD = 300


class AutonomicWorkspaceIndexer:
    """Scans and indexes repository structures deterministically."""
    def __init__(self, root_dir: str) -> None:
        self.root_dir = root_dir
        self.topology = ProjectTopology()
        # Captures standard C++ and Lua logging tools
        self._log_patterns = re.compile(
            r"(?:spdlog::|fmt::print|printf|SDL_Log|print\s*\()",
            re.IGNORECASE
        )
        # Captures concrete C++ class and struct definitions
        self._class_pattern = re.compile(
            r"^\s*(?:class|struct)\s+([A-Z][a-zA-Z0-9_]+)",
            re.MULTILINE
        )
        # Captures C++ free function definitions at file scope (non-indented)
        self._cpp_func_pattern = re.compile(
            r"^(?:(?:static|inline|virtual|explicit|constexpr)\s+)*"
            r"[\w:*&<>]+\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
            re.MULTILINE
        )
        # Captures Lua function definitions: `function Name(` or `Name = function(`
        self._lua_func_pattern = re.compile(
            r"(?:^function\s+([a-zA-Z_][a-zA-Z0-9_.]*)\s*\("
            r"|^([a-zA-Z_][a-zA-Z0-9_.]*)\s*=\s*function\s*\()",
            re.MULTILINE
        )
        # Captures top-level Lua module tables: `MyModule = {}`
        self._lua_module_pattern = re.compile(
            r"^([A-Z][a-zA-Z0-9_]*)\s*=\s*\{",
            re.MULTILINE
        )

    def scan_project(self) -> ProjectTopology:
        self.topology = ProjectTopology()

        for root, _, files in os.walk(self.root_dir):
            # Skip safe offload stores, global caches, and hidden directories
            if any(
                part.startswith('.') or part in ('offload_store', 'global_cache')
                for part in root.split(os.sep)
            ):
                continue

            for file_name in files:
                ext = os.path.splitext(file_name)[1].lower()
                file_path = os.path.join(root, file_name)

                if ext in ('.h', '.hpp', '.cpp'):
                    self._analyze_cpp_node(file_path, ext)
                elif ext == '.lua':
                    self._analyze_lua_node(file_path)

        return self.topology

    def _analyze_cpp_node(self, file_path: str, ext: str) -> None:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            rel_path = os.path.relpath(file_path, self.root_dir)
            lines = content.splitlines()
            line_count = len(lines)

            # Extract concrete AST class symbols to eliminate naming hallucinations
            classes = []
            for match in self._class_pattern.finditer(content):
                name = match.group(1)
                self.topology.classes.add(name)
                classes.append(name)

            # Extract free function signatures (non-indented definitions)
            _SKIP_KEYWORDS = {
                "if", "while", "for", "switch", "return", "sizeof",
                "alignof", "decltype", "static_assert",
            }
            functions = []
            for match in self._cpp_func_pattern.finditer(content):
                name = match.group(1)
                if name and name not in _SKIP_KEYWORDS and name not in classes:
                    functions.append(name)
            # Deduplicate preserving order
            seen: Set[str] = set()
            functions = [f for f in functions if not (f in seen or seen.add(f))]

            entry = FileASTEntry(
                rel_path=rel_path,
                line_count=line_count,
                classes=classes,
                functions=functions[:20],
                is_large=line_count > LARGE_FILE_THRESHOLD,
            )
            self.topology.file_index[rel_path] = entry

            # Verify observability instrumentation exclusively for implementations
            if ext == '.cpp' and not self._log_patterns.search(content):
                self.topology.uninstrumented_files.append(rel_path)
        except Exception:
            pass

    def _analyze_lua_node(self, file_path: str) -> None:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            rel_path = os.path.relpath(file_path, self.root_dir)
            lines = content.splitlines()
            line_count = len(lines)

            # Extract Lua function names
            functions = []
            for match in self._lua_func_pattern.finditer(content):
                name = match.group(1) or match.group(2)
                if name:
                    functions.append(name)
            seen: Set[str] = set()
            functions = [f for f in functions if not (f in seen or seen.add(f))]

            # Extract Lua module tables
            lua_modules = [m.group(1) for m in self._lua_module_pattern.finditer(content)]

            entry = FileASTEntry(
                rel_path=rel_path,
                line_count=line_count,
                functions=functions[:20],
                lua_modules=lua_modules[:10],
                is_large=line_count > LARGE_FILE_THRESHOLD,
            )
            self.topology.file_index[rel_path] = entry

            if not self._log_patterns.search(content):
                self.topology.uninstrumented_files.append(rel_path)
        except Exception:
            pass


# CRITICAL KERNEL EXPORT: This assignment MUST remain completely unindented at the module level.
WORKSPACE_INDEXER = AutonomicWorkspaceIndexer(os.getcwd())
