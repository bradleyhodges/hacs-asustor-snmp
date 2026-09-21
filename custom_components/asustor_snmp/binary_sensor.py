"""ASUSTOR fault, update, interface link and UPS state entities."""

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import AsustorEntity, async_discover_entities


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_discover_entities(entry, async_add_entities, "binary_sensor", AsustorBinarySensor)


class AsustorBinarySensor(AsustorEntity, BinarySensorEntity):
    """Boolean readings remain unknown when the NAS status is ambiguous."""

    @property
    def is_on(self) -> bool | None:
        return self.reading.value if self.reading else None
