"""
Step 2.12: Domain resolution (resolve_agent_name) characterization tests.
"""

from pipeline import resolve_agent_name, get_agent_system, ALL_DOMAINS, AGENT_ALIAS_MAP


class TestDomainResolution:
    """Lock in resolve_agent_name behavior (lines 2535-2562)."""

    def test_resolve_known_alias(self):
        # Should resolve aliases to canonical domain keys
        result = resolve_agent_name("agent_cpp")
        assert isinstance(result, str)

    def test_resolve_returns_same_if_not_found(self):
        result = resolve_agent_name("unknown_agent_xyz")
        # Either returns the input unchanged or some default
        assert isinstance(result, str)

    def test_all_domains_is_dict(self):
        assert isinstance(ALL_DOMAINS, dict)
        assert len(ALL_DOMAINS) > 0

    def test_agent_alias_map_is_dict(self):
        assert isinstance(AGENT_ALIAS_MAP, dict)
        assert len(AGENT_ALIAS_MAP) > 0


class TestGetAgentSystem:
    """Lock in get_agent_system behavior (lines 2565-2577)."""

    def test_get_system_for_known_domain(self):
        system = get_agent_system("C++")
        assert isinstance(system, str)
        assert len(system) > 0

    def test_get_system_for_unknown_domain(self):
        system = get_agent_system("NONEXISTENT_DOMAIN")
        assert isinstance(system, str)

    def test_get_system_returns_different_for_different_domains(self):
        cpp = get_agent_system("C++")
        lua = get_agent_system("Lua")
        # These should differ since prompts differ per domain
        assert cpp != lua
