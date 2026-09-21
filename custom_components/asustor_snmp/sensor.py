"""ASUSTOR system, storage, interface and UPS sensor entities."""

from homeassistant.components.sensor import SensorEntity

from .entity import AsustorEntity, async_discover_entities


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_discover_entities(entry, async_add_entities, "sensor", AsustorSensor)


class AsustorSensor(AsustorEntity, SensorEntity):
    """Expose a normalized reading with native HA units and statistics metadata."""

    def __init__(self, coordinator, reading) -> None:
        super().__init__(coordinator, reading)
        self._attr_native_unit_of_measurement = reading.unit
        self._attr_state_class = reading.state_class
        if reading.state_class == "measurement":
            self._attr_suggested_display_precision = 2 if reading.unit in ("%", "Mbit/s") else 0

    @property
    def native_value(self):
        return self.reading.value if self.reading else None
