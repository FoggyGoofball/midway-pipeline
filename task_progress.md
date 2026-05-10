# Refactoring Progress - Multi-Agent Context Bleed & Stream Resilience

## Todo Checklist

- [ ] Explore existing codebase: _prompts.py, fix_mesh.py, mesh_loops.py, pipeline_stream_server.py, ollama_client.py, domain_registry.py, context_extractor.py
- [ ] **Directive A**: File-Level Sandboxing (Domain Enforcement)
  - [ ] Add allowed_extensions constraint to system prompts
  - [ ] Update parsing logic to reject cross-domain file writes
- [ ] **Directive B**: Differential Enforcement of Mandates (Reviewer Constraint)
  - [ ] Inject constraint into reviewer prompt: mandates apply only to new/modified code
- [ ] **Directive C**: Context Pruning in Fix Cycles
  - [ ] Implement context-pruning function for FAIL verdict critiques
  - [ ] Strip iterative generation attempts; provide only current file state + relevant error
- [ ] **Directive D**: Stream Resilience and State Rollback
  - [ ] Wrap HTTP streaming in try/except ConnectionResetError
  - [ ] Implement rollback to pre-task snapshot on socket drop
  - [ ] Add VRAM cooldown + temperature decrement + auto-retry
- [ ] Verify all patches integrate cleanly
