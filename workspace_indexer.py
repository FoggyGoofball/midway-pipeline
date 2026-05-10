import os
import re
from typing import List, Set

class ProjectTopology:
    def __init__(self) -> None:
        self.classes: Set[str] = set()
        self.uninstrumented_files: List[str] = []

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

    def scan_project(self) -> ProjectTopology:
        self.topology = ProjectTopology()
        
        for root, _, files in os.walk(self.root_dir):
            # Skip safe offload stores, global caches, and hidden directories
            if any(part.startswith('.') or part in ('offload_store', 'global_cache') for part in root.split(os.sep)):
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
                
            # Extract concrete AST class symbols to eliminate naming hallucinations
            for match in self._class_pattern.finditer(content):
                self.topology.classes.add(match.group(1))
                
            # Verify observability instrumentation exclusively for implementations
            if ext == '.cpp' and not self._log_patterns.search(content):
                rel_path = os.path.relpath(file_path, self.root_dir)
                self.topology.uninstrumented_files.append(rel_path)
        except Exception:
            pass

    def _analyze_lua_node(self, file_path: str) -> None:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            if not self._log_patterns.search(content):
                rel_path = os.path.relpath(file_path, self.root_dir)
                self.topology.uninstrumented_files.append(rel_path)
        except Exception:
            pass

# CRITICAL KERNEL EXPORT: This assignment MUST remain completely unindented at the module level.
WORKSPACE_INDEXER = AutonomicWorkspaceIndexer(os.getcwd())
