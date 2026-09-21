"""Shared entity metadata and discovery of new table rows while running."""

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AsustorCoordinator
from .telemetry import Reading


class AsustorEntity(CoordinatorEntity[AsustorCoordinator]):
    """An entity with NAS-serial-based identity and per-reading availability."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AsustorCoordinator, reading: Reading) -> None:
        super().__init__(coordinator)
        self.key = reading.key
        self._attr_unique_id = f"{coordinator.data.serial}_{self.key}"
        self._attr_name = reading.name
        self._attr_entity_registry_enabled_default = reading.enabled
        self._attr_entity_category = EntityCategory.DIAGNOSTIC if reading.diagnostic else None
        self._attr_device_class = reading.device_class

    @property
    def device_info(self) -> DeviceInfo:
        """Keep firmware and model metadata current after an ADM update."""
        data = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, data.serial)},
            name=self.coordinator.config_entry.title,
            manufacturer="ASUSTOR",
            model=data.model,
            sw_version=data.version,
            serial_number=data.serial,
        )

    @property
    def available(self) -> bool:
        return super().available and self.key in self.coordinator.data.readings

    @property
    def reading(self) -> Reading | None:
        return self.coordinator.data.readings.get(self.key)

    @property
    def extra_state_attributes(self):
        return self.reading.attributes if self.reading else None


@callback
def async_discover_entities(entry, async_add_entities, platform, entity_class) -> None:
    """Discover each stable key once; removed rows become unavailable."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def discover() -> None:
        if not coordinator.last_update_success:
            return
        added = []
        for key, reading in coordinator.data.readings.items():
            if reading.platform == platform and key not in known:
                known.add(key)
                added.append(entity_class(coordinator, reading))
        if added:
            async_add_entities(added)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))
