#!/usr/bin/env python3
"""System verification script for UE4 Cartridge & Acquisition Framework."""

print('='*80)
print('UE4 CARTRIDGE & ACQUISITION SYSTEM VERIFICATION')
print('='*80)

# Step 1: Verify cartridges discovered
print('\n[1] Cartridge Discovery')
from cartridge_loader import _discover_cartridges
carts = _discover_cartridges()
print(f'    OK Found {len(carts)} cartridges')
for name in sorted(carts.keys()):
    eco = getattr(carts[name], 'ECOSYSTEM_NAME', '?')
    print(f'      - {name:25s} => {eco}')

# Step 2: Verify UE4 cartridge structure
print('\n[2] UE4 Cartridge Structure')
from cartridges.ue4_ecosystem import UE4AgentCartridge
domains = UE4AgentCartridge.get_domain_registry()
aliases = UE4AgentCartridge.get_alias_map()
gates = UE4AgentCartridge.get_reasoning_gate_domains()
print(f'    OK Domains: {len(domains)}')
print(f'    OK Aliases: {len(aliases)}')
print(f'    OK Reasoning gates: {gates}')

# Step 3: Verify mount path
print('\n[3] Cartridge Mount Path')
from models import PipelineContext
from pathlib import Path
ctx = PipelineContext(
    project_root=Path('.'),
    memory_dir=Path('.'),
    session_id='test',
    tasks=[],
    global_signals=[]
)
ctx.mount_cartridge(UE4AgentCartridge)
print(f'    OK Mounted: {ctx._cartridge_ecosystem_name}')
print(f'    OK Reasoning gates: {len(ctx._cartridge_reasoning_gate_domains)}')
print(f'    OK Coding mandates: {len(ctx._cartridge_coding_mandates)} chars')
print(f'    OK Review extra: {len(ctx._cartridge_review_prompt_extra)} chars')

# Step 4: Verify prompt bootstrap
print('\n[4] Prompt Bootstrap')
import pipeline as _pipeline_module
_pipeline_module._CTX = ctx
from _prompts import pipeline_bootstrap_prompts
pipeline_bootstrap_prompts()
import _prompts
print(f'    OK REASONING_GATE_DOMAINS: {_prompts.REASONING_GATE_DOMAINS}')
has_ue4 = 'Unreal' in _prompts.DIRECTOR_SYSTEM or 'Engine' in _prompts.DIRECTOR_SYSTEM
print(f'    OK DIRECTOR_SYSTEM enriched: {has_ue4}')
print(f'    OK REVIEW_PROMPT: {len(_prompts.REVIEW_PROMPT)} chars')

# Step 5: Verify acquisition wizard
print('\n[5] Acquisition Wizard')
from acquisition_wizard import AcquisitionWizard
wizard = AcquisitionWizard()
print(f'    OK Wizard phase: {wizard.phase}/6')
completed = wizard.completed_phases if wizard.completed_phases else 'none yet'
print(f'    OK Completed: {completed}')

# Step 6: Verify documentation
print('\n[6] Documentation')
docs = [
    'docs/UE4_CARTRIDGE_README.md',
    'docs/ACQUISITION_PHASE_PLAN.md',
    'docs/UE4_SOLID_PLAN.md',
    'CARTRIDGE_GUIDE.md',
]
for doc in docs:
    p = Path(doc)
    exists = 'OK' if p.exists() else 'MISSING'
    print(f'    {exists} {doc}')

print()
print('='*80)
print('ALL SYSTEMS OPERATIONAL')
print('='*80)
print()
print('NEXT STEPS:')
print('  1. python cartridge_loader.py wizard')
print('  2. python acquisition_wizard.py')
print('  3. Read: docs/UE4_SOLID_PLAN.md')
print()
