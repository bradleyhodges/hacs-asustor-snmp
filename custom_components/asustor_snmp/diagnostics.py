"""Privacy-preserving diagnostics: no credentials, addresses or raw SNMP data."""

from collections import Counter

from .const import DEFAULT_OPTIONS, VERSION


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    """Return an allowlist only; arbitrary vendor strings can contain identifiers."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "integration_version": VERSION,
        "last_update_success": coordinator.last_update_success,
        "network_available": coordinator.client.network_available,
        "options": DEFAULT_OPTIONS | dict(entry.options),
        "snmp": {
            key: entry.data.get(key)
            for key in ("version", "security_level", "auth_protocol", "priv_protocol")
        },
        "entity_counts": dict(Counter(r.platform for r in data.readings.values())),
        "unknown_readings": sum(r.value is None for r in data.readings.values()),
        "network_interface_count": len(data.network),
    }
