"""Switch platform for the BLE Generic Device integration."""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import BLEDeviceCoordinator
from .const import DOMAIN, BLEDeviceNotAvailable

_LOGGER = logging.getLogger(__name__)


class BLECharSwitch(CoordinatorEntity, SwitchEntity, RestoreEntity):
    """A BLE characteristic represented as a switch."""

    def __init__(
        self,
        coordinator: BLEDeviceCoordinator,
        name: str,
        char_uuid: str,
        entry,
    ):
        """Initialize the BLE switch."""
        super().__init__(coordinator)
        self._attr_name = name
        self._char_uuid = char_uuid
        self._entry = entry
        self._attr_is_on = False
        self._attr_unique_id = (
            f"{coordinator.ble_device.address.replace(':', '').lower()}_{char_uuid[-8:]}"
        )

    async def async_added_to_hass(self):
        """Restore last known state."""
        await super().async_added_to_hass()
        
        # Restore last state
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state == "on"
            _LOGGER.debug(
                "[%s] Restored state for %s: %s",
                self.coordinator.ble_device.address,
                self._attr_name,
                self._attr_is_on,
            )


    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Log availability changes
        _LOGGER.debug(
            "[%s] %s coordinator update, available=%s",
            self.coordinator.ble_device.address,
            self._attr_name,
            self.coordinator.available,
        )
        super()._handle_coordinator_update()

    async def _async_write_with_availability_check(self, value: bytes, action: str):
        """Write value with availability check and error handling."""
        device_addr = self.coordinator.ble_device.address
        
        try:
            # Pre-check: is coordinator available?
            if not self.coordinator.available:
                _LOGGER.warning(
                    "[%s] Cannot %s %s - coordinator reports unavailable",
                    device_addr,
                    action,
                    self._attr_name,
                )
                raise HomeAssistantError(
                    f"Device {device_addr} is not available"
                )
            
            _LOGGER.debug(
                "[%s] Attempting to %s %s (writing %s to %s)",
                device_addr,
                action,
                self._attr_name,
                value.hex(),
                self._char_uuid,
            )
            
            # Attempt the write
            await self.coordinator.connection_mgr.write(self._char_uuid, value)
            
            _LOGGER.info(
                "[%s] Successfully %s %s",
                device_addr,
                action,
                self._attr_name,
            )
            
        except BLEDeviceNotAvailable as err:
            _LOGGER.error(
                "[%s] Device not available for %s %s: %s",
                device_addr,
                action,
                self._attr_name,
                err,
            )
            
            # Mark coordinator as unavailable - this will affect ALL entities
            self.coordinator.mark_write_failed()
            
            raise HomeAssistantError(
                f"Device not available: {err}"
            ) from err
            
        except Exception as err:
            _LOGGER.error(
                "[%s] Unexpected error %s %s: %s (%s)",
                device_addr,
                action,
                self._attr_name,
                err,
                type(err).__name__,
            )
            # Don't mark coordinator unavailable for unexpected errors
            raise

    async def async_turn_on(self, **kwargs):
        """Turn the switch on."""
        await self._async_write_with_availability_check(b"\x01", "turn on")
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the switch off."""
        await self._async_write_with_availability_check(b"\x00", "turn off")
        self._attr_is_on = False
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        is_available = self.coordinator.available
        _LOGGER.debug(
            "[%s] %s availability check: %s",
            self.coordinator.ble_device.address,
            self._attr_name,
            is_available,
        )
        return is_available

    @property
    def device_info(self):
        """Return device metadata for the UI."""
        data = getattr(self._entry, "data", {})
        name = data.get("name", f"BLE Device {self.coordinator.ble_device.address}")
        manufacturer = data.get("manufacturer", "Custom BLE")
        
        return {
            "identifiers": {(DOMAIN, self.coordinator.ble_device.address)},
            "connections": {("bluetooth", self.coordinator.ble_device.address)},
            "name": name,
            "manufacturer": manufacturer,
        }


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up BLE switches for each configured characteristic."""
    from .const import CONF_CHARS
    
    # Get coordinator from hass.data
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    # Get characteristics from config
    data = {**entry.data, **(entry.options or {})}
    chars = data.get(CONF_CHARS, [])

    if not chars:
        _LOGGER.warning(
            "[%s] No characteristics defined. Use Configure to add characteristics.",
            coordinator.ble_device.address,
        )
        await _async_remove_orphaned_entities(hass, entry, set())
        return

    # Create entities for current characteristics
    entities = [
        BLECharSwitch(coordinator, ch["name"], ch["uuid"], entry)
        for ch in chars
    ]
    
    # Get current unique IDs
    current_unique_ids = {entity.unique_id for entity in entities}
    
    # Remove entities that are no longer in the config
    await _async_remove_orphaned_entities(hass, entry, current_unique_ids)
    
    _LOGGER.info(
        "[%s] Adding %d BLE switch entities",
        coordinator.ble_device.address,
        len(entities),
    )
    async_add_entities(entities)


async def _async_remove_orphaned_entities(
    hass, entry, current_unique_ids: set[str]
):
    """Remove entities that are no longer in the config."""
    ent_reg = er.async_get(hass)
    
    # Find all entities for this config entry
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    
    removed_count = 0
    for entity_entry in entities:
        if entity_entry.unique_id not in current_unique_ids:
            _LOGGER.info(
                "Removing orphaned entity: %s (unique_id: %s)",
                entity_entry.entity_id,
                entity_entry.unique_id,
            )
            ent_reg.async_remove(entity_entry.entity_id)
            removed_count += 1
    
    if removed_count > 0:
        _LOGGER.info("Removed %d orphaned entities", removed_count)
