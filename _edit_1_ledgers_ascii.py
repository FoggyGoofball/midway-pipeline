"""
Surgical edit #1: Add 'ledger' key to each ALL_DOMAINS entry + create initial ledger files.
Run: python _edit_1_ledgers.py
"""
import re, os

PATH = r'c:\Users\Admin\source\repos\midway\pipeline.py'
MEMORY_DIR = r'c:\Users\Admin\source\repos\midway\docs\memory'

with open(PATH, 'r', encoding='utf-8', errors='replace') as f:
    raw = f.read()

#  Inject 'ledger' key after 'description' in each domain entry 
domain_ledgers = {
    'C++':  'docs/memory/cpp_ledger.md',
    'PHYS': 'docs/memory/phys_ledger.md',
    'SHADER': 'docs/memory/shader_ledger.md',
    'Lua':  'docs/memory/lua_ledger.md',
    'DOC':  'docs/memory/doc_ledger.md',
    'CONF': 'docs/memory/conf_ledger.md',
}

changes = 0
for domain_key, ledger_path in domain_ledgers.items():
    # Check if ledger already injected
    if f'ledger": "{ledger_path}"' in raw:
        print(f'   {domain_key} ledger already set')
        continue

    # Build the regex: find the description line for this specific domain
    # Pattern: after `"description": "...",` insert `"ledger": "docs/memory/..._ledger.md",`
    # We need to find description belonging to THIS domain, not another
    # Strategy: find domain header, then find the description within that section

    # Find the position of domain header
    header_marker = f'"{domain_key}": {{'
    header_pos = raw.find(header_marker)
    if header_pos == -1:
        print(f'   Domain header "{domain_key}" not found')
        continue
    
    # Find the next description line after this header
    search_from = header_pos
    desc_marker = '"description":'
    desc_pos = raw.find(desc_marker, search_from)
    if desc_pos == -1:
        print(f'   description not found for {domain_key}')
        continue
    
    # Find end of description line (the comma at the end)
    eol_pos = raw.find('\n', desc_pos)
    desc_line = raw[desc_pos:eol_pos]
    
    # Insert ledger after the description line end
    indent = '        '
    ledger_line = f'{indent}"ledger": "{ledger_path}",'
    before = raw[:eol_pos+1]
    after = raw[eol_pos+1:]
    raw = before + ledger_line + '\n' + after
    changes += 1
    print(f'   Inserted ledger for {domain_key}')

print(f'\n--- Applied {changes} ledger insertions ---')

# Write back
with open(PATH, 'w', encoding='utf-8', errors='replace') as f:
    f.write(raw)
print(f' pipeline.py saved')

#  Create initial ledger files 
os.makedirs(MEMORY_DIR, exist_ok=True)

ledger_templates = {
    'cpp_ledger.md': """# C++ Core Ledger
> Persistent memory bank for the C++17 systems engineer.
> Append new entries here whenever a core loop, global variable, or architectural decision is finalized.

## Table of Contents
<!-- Entries are added dynamically by agents -->

""",
    'phys_ledger.md': """# Physics Architect Ledger
> Persistent memory bank for the Lead Physics Architect.
> Append entries for physics system decisions, collision layer definitions, teleport stability logic.

## Table of Contents
<!-- Entries are added dynamically by agents -->

""",
    'shader_ledger.md': """# Shader Expert Ledger
> Persistent memory bank for the Rendering Expert.
> Append entries for GLSL shader pipelines, VBO management, rendering decisions.

## Table of Contents
<!-- Entries are added dynamically by agents -->

""",
    'lua_ledger.md': """# Lua Scripter Ledger
> Persistent memory bank for the gameplay scripter.
> Append entries for attraction scripts, UI patterns, modifier consumption logic.

## Table of Contents
<!-- Entries are added dynamically by agents -->

""",
    'doc_ledger.md': """# Code Documentarian Ledger
> Persistent memory bank for API documentation decisions.
> Append entries for API clarifications, corrections, contract updates.

## Table of Contents
<!-- Entries are added dynamically by agents -->

""",
    'conf_ledger.md': """# Conflict Resolution Ledger
> Persistent memory bank for conflict mediation decisions.
> Append entries for VETO/OBJECT precedents and resolution patterns.

## Table of Contents
<!-- Entries are added dynamically by agents -->

""",
    'architecture_ledger.md': """# Director / Architecture Ledger
> Persistent memory bank for the Project Director.
> Append entries for architectural decisions, task decomposition patterns, system-wide design choices.

## Table of Contents
<!-- Entries are added dynamically by agents -->

""",
}

for fname, content in ledger_templates.items():
    fpath = os.path.join(MEMORY_DIR, fname)
    if not os.path.exists(fpath):
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'   Created {fname}')
    else:
        print(f'   {fname} already exists')

print('\n Task 1 complete: Ledgers initialized')
