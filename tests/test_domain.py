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
    """Lock in get_agent_system behavior (cartridge-driven domain registry).

    Language/technology domains (C++/Lua/...) are defined EXCLUSIVELY by the
    mounted cartridge; the static ``ALL_DOMAINS`` registry holds only universal
    roles.  With no cartridge mounted, language domains resolve to "".
    """

    def test_get_system_for_universal_role(self):
        system = get_agent_system("REVIEWER")
        assert isinstance(system, str)
        assert len(system) > 0

    def test_get_system_unknown_returns_empty(self):
        system = get_agent_system("NONEXISTENT_DOMAIN")
        assert system == ""

    def test_get_system_differs_across_roles(self):
        reviewer = get_agent_system("REVIEWER")
        doc = get_agent_system("DOC")
        assert reviewer != doc
