"""Coordinate one consistent polling snapshot for every NAS entity."""

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_OPTIONS, DOMAIN
from .snmp import NotAsustorError, SnmpAuthError, SnmpClient, SnmpError
from .telemetry import Snapshot, build_snapshot

_LOGGER = logging.getLogger(__name__)


class AsustorCoordinator(DataUpdateCoordinator[Snapshot]):
    """Never publish old data as current after a failed request."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.options = DEFAULT_OPTIONS | dict(entry.options)
        self.client = SnmpClient(entry.data, self.options)
        self._network = {}
        self._poll_task = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=self.options["scan_interval"]),
        )

    async def _async_update_data(self) -> Snapshot:
        try:
            self._poll_task = asyncio.create_task(self.client.async_fetch())
            raw = await self._poll_task
            snapshot = build_snapshot(
                raw, now=monotonic(), previous=self._network, options=self.options
            )
            if snapshot.serial != self.config_entry.unique_id:
                self.client.close()
                self._network = {}
                raise ConfigEntryError("The NAS serial number no longer matches this integration")
        except SnmpAuthError as exc:
            self._network = {}
            self.client.close()
            raise ConfigEntryAuthFailed(
                "SNMP credentials or security settings were rejected"
            ) from exc
        except NotAsustorError as exc:
            self._network = {}
            self.client.close()
            raise UpdateFailed("ASUSTOR identity is missing from the SNMP response") from exc
        except (SnmpError, TimeoutError, OSError) as exc:
            self._network = {}
            self.client.close()
            raise UpdateFailed(
                "Unable to retrieve ASUSTOR telemetry; retrying on the next poll"
            ) from exc
        finally:
            self._poll_task = None
        self._network = snapshot.network
        return snapshot

    async def async_shutdown(self) -> None:
        """Stop scheduled work and release transport resources."""
        await super().async_shutdown()
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
        self.client.close()
