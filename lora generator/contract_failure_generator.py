"""
contract_failure_generator.py — derive failure-inducing mutations from the
bridge contract, programmatically.

The hand-written MUTATIONS in failure_mode_seeder.py are a bootstrap.  The
forward-looking path: the acquisition wizard already collects a per-cartridge
contract; PARSE that contract and derive the contract-dependent failure modes
from its API surface — no hand-writing per cartridge.

Contract-derived (per-cartridge, from the API surface):
  - bare namespace calls : every namespaced symbol emitted bare  -> Fix #6
  - arity over / under   : every Spawn*/impulse signature         -> Fix G2
  - phantom members      : a real member name perturbed           -> Fix #8/#22/C19
  - modifier lowercase   : every modifier_globals key via mods.<k> -> Fix #9

Language-syntax failure modes are a STATIC catalog (properties of the language,
not the contract) and stay in failure_mode_seeder.py.

Usage
-----
  python "lora generator/contract_failure_generator.py" --selftest <clean_file.lua>
  python "lora generator/contract_failure_generator.py" --count
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
_REPO_ROOT = SCRIPT_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def parse_signature(sig: str) -> Tuple[str, List[str]]:
    """Parse a contract signature like ``"SpawnStaticBox(lx, ly, lz, w, h, d) -> handle"``
    into ``(name, [params])``.  Optional ``[ ... ]`` segments are dropped so the
    arity is the REQUIRED count."""
    name = sig.split("(", 1)[0].strip()
    inner = sig.split("(", 1)[1].split(")", 1)[0] if "(" in sig else ""
    inner = re.sub(r"\[[^\]]*\]", "", inner)  # drop optional [mass] etc.
    params = [p.strip() for p in inner.split(",") if p.strip()]
    return name, params


def _append(code: str, snippet: str) -> str:
    return (code.rstrip() + "\n\n" + snippet + "\n")


def _dummy(params: List[str]) -> str:
    # A positionally-plausible argument list: strings for `name`, 0 elsewhere.
    args = []
    for p in params:
        if "name" in p.lower():
            args.append("'pucks'")
        elif "label" in p.lower() or "path" in p.lower():
            args.append("'x'")
        else:
            args.append("0")
    return ", ".join(args)


def _bare(name: str, params: List[str]) -> Callable[[str], str]:
    short = name.rsplit(".", 1)[-1]
    call = f"{short}({_dummy(params)})"

    def mutate(code: str) -> str:
        return _append(code, f"local _ = {call}\n")

    return mutate


def _arity_over(ns: str, name: str, params: List[str]) -> Callable[[str], str]:
    short = name.rsplit(".", 1)[-1]
    call = f"{ns}.{short}({_dummy(params)}, 9)"

    def mutate(code: str) -> str:
        return _append(code, f"{call}\n")

    return mutate


def _arity_under(ns: str, name: str, params: List[str]) -> Callable[[str], str]:
    short = name.rsplit(".", 1)[-1]
    args = _dummy(params[:-1])  # drop the last required arg
    call = f"{ns}.{short}({args})"

    def mutate(code: str) -> str:
        return _append(code, f"{call}\n")

    return mutate


def _phantom(ns: str, name: str) -> Callable[[str], str]:
    short = name.rsplit(".", 1)[-1]
    fake = short + "X"  # perturb a real member -> guaranteed unknown

    def mutate(code: str) -> str:
        return _append(code, f"{ns}.{fake}(0)\n")

    return mutate


def _modifier_lowercase(key: str) -> Callable[[str], str]:
    lower = key.lower()

    def mutate(code: str) -> str:
        return _append(code, f"local x = mods.{lower}\n")

    return mutate


def _load_contract() -> dict:
    from cartridges.midway_data_refs import build_bridge_contract
    return build_bridge_contract()


@dataclass
class GuardRule:
    """A single contract-derived failure mode WITH its arbiter metadata.

    This is the deterministic bridge between the bridge contract and BOTH
    consumers of that knowledge:
      - the fixer/failure corpus (via ``mutate`` -> broken code), and
      - the arbiter training-data generator (via ``objection`` + ``question``).
    """
    name: str
    category: str                       # bare | arity_over | arity_under | phantom | mods_lower
    mutate: Callable[[str], str]        # clean -> broken code producer
    objection: str                      # REJECT justification citing the defect
    question: Optional[str] = None      # oracle question (None = no deterministic fact)


def derive_guard_rules(contract: dict = None) -> Dict[str, GuardRule]:
    """Programmatically derive contract-dependent failure modes WITH their
    arbiter metadata (objection + oracle question).

    Mirrors ``derive_contract_mutations`` but returns full ``GuardRule``
    objects so the arbiter dataset generator no longer needs a hand-written
    objection/question table for the contract-derived modes.
    """
    contract = contract or _load_contract()
    out: Dict[str, GuardRule] = {}

    def _add(name: str, category: str, mutate: Callable[[str], str],
             objection: str, question: Optional[str]) -> None:
        out[name] = GuardRule(name=name, category=category, mutate=mutate,
                              objection=objection, question=question)

    # 1. Bare namespace calls + arity for every spawn API.
    for sig in contract.get("midwayphysics_spawn_api", {}):
        name, params = parse_signature(sig)
        if not params:
            continue
        short = name.rsplit(".", 1)[-1]
        _add(f"bare_{short}", "bare", _bare(name, params),
             f"bare call `{short}(...)` must be namespace-qualified as MidwayPhysics.{short}",
             f"Is {short} a real API?")
        _add(f"arity_over_{short}", "arity_over", _arity_over("MidwayPhysics", name, params),
             f"`{short}` is called with too many arguments",
             f"How many arguments does {short} take?")
        if len(params) >= 2:
            _add(f"arity_under_{short}", "arity_under", _arity_under("MidwayPhysics", name, params),
                 f"`{short}` is called with too few arguments",
                 f"How many arguments does {short} take?")

    # 2. Bare + phantom for the economy (Engine) and input (MidwayInput) APIs.
    for section, ns in (
        ("economy_api", "Engine"),
        ("input_api", "MidwayInput"),
    ):
        for sig in contract.get(section, {}):
            name, params = parse_signature(sig)
            if not params:
                continue
            short = name.rsplit(".", 1)[-1]
            _add(f"bare_{short}", "bare", _bare(name, params),
                 f"bare call `{short}(...)` must be namespace-qualified as {ns}.{short}",
                 f"Is {short} a real API?")
            _add(f"phantom_{short}", "phantom", _phantom(ns, name),
                 f"phantom API `{ns}.{short}X` is not in the bridge contract",
                 f"Is {ns}.{short}X a real API?")

    # 3. Phantom members for the spawn namespace itself.
    for sig in contract.get("midwayphysics_spawn_api", {}):
        name, _ = parse_signature(sig)
        short = name.rsplit(".", 1)[-1]
        _add(f"phantom_{short}", "phantom", _phantom("MidwayPhysics", name),
             f"phantom API `MidwayPhysics.{short}X` is not in the bridge contract",
             f"Is MidwayPhysics.{short}X a real API?")

    # 4. Modifier lowercase for every modifier global key.
    for key in contract.get("modifier_globals", {}):
        _add(f"mods_lower_{key.lower()}", "mods_lower", _modifier_lowercase(key),
             f"lowercase `mods.{key.lower()}` is not declared; modifiers must be read "
             f"via MOD or AttractionConstants.modifiers",
             None)

    return out


def derive_contract_mutations(contract: dict = None) -> Dict[str, Callable[[str], str]]:
    """Backward-compatible wrapper: ``{name: mutator}`` only.

    Kept so existing callers/tests that only need the broken-code producer
    continue to work unchanged.
    """
    return {name: rule.mutate for name, rule in derive_guard_rules(contract).items()}


def run_mutations(file_paths: list[str]) -> int:
    """Apply every derived mutation to every file through the observed pass
    (source="initial"), recording fixer deltas to the INITIAL corpus."""
    os.environ.setdefault("MIDWAY_FAILURE_CORPUS", "1")
    from _post_process_lua import post_process_lua_observed
    from failure_corpus import read_corpus, corpus_path
    _corpus = corpus_path("initial")
    before = len(read_corpus(_corpus))
    mutations = derive_contract_mutations()
    for fp in file_paths:
        if not Path(fp).is_file():
            print(f"  SKIP missing: {fp}")
            continue
        code = Path(fp).read_text(encoding="utf-8")
        rel = Path(fp).name
        for name, mutate in mutations.items():
            post_process_lua_observed(mutate(code), file_relpath=rel, source="initial")
    after = len(read_corpus(_corpus))
    return after - before


def selftest(file_path: str) -> None:
    from _post_process_lua import post_process_lua
    code = Path(file_path).read_text(encoding="utf-8")
    mutations = derive_contract_mutations()
    delta = 0
    noop = []
    for name, mutate in mutations.items():
        broken = mutate(code)
        fixed = post_process_lua(broken)
        if fixed != broken:
            delta += 1
        else:
            noop.append(name)
    print(f"  derived {len(mutations)} mutations; {delta} produce a fixer delta; "
          f"{len(noop)} no-op")
    if noop:
        print("  no-op mutations (fixer does not cover): " + ", ".join(noop[:20]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", help="clean Lua file to test derived mutations against")
    ap.add_argument("--count", action="store_true", help="print the derived mutation count")
    ap.add_argument("--mutate", nargs="+", help="clean Lua files to mutate + record to the initial corpus")
    args = ap.parse_args()

    if args.mutate:
        n = run_mutations(args.mutate)
        print(f"  [contract-seed] {n} new initial-corpus record(s).")
        return
    mutations = derive_contract_mutations()
    if args.count:
        print(f"derived {len(mutations)} contract mutations")
    if args.selftest:
        selftest(args.selftest)
    if not args.count and not args.selftest:
        ap.print_help()


if __name__ == "__main__":
    main()
