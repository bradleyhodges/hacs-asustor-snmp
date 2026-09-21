"""Read-only ASUSTOR NAS monitoring over authenticated SNMP."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import AsustorCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
type AsustorConfigEntry = ConfigEntry[AsustorCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AsustorConfigEntry) -> bool:
    """Set up after one successful poll; close resources on every failure path."""
    coordinator = AsustorCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_shutdown()
        raise

    async def async_stop(_event):
        await coordinator.async_shutdown()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AsustorConfigEntry) -> bool:
    """Unload both platforms before shutting down their coordinator."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
        return True
    return False
