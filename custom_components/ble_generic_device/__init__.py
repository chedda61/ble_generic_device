"""Support for BLE Generic Device integration."""
from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.const import Platform

from .const import DOMAIN, CONF_MAC, CONF_DELAY, DISCONNECT_DELAY
from .coordinator import BLEDeviceCoordinator
from .connection_manager import ConnectionManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH]


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the BLE Generic Device component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BLE Generic Device from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    data = {**entry.data, **(entry.options or {})}
    address: str = data[CONF_MAC]
    
    if entry.unique_id is None:
        hass.config_entries.async_update_entry(
            entry, unique_id=address.replace(":", "").lower()
        )
        _LOGGER.info("[%s] Set unique_id for config entry", address)
    
    # Get BLE device
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), connectable=True
    )
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find BLE Device with address {address}"
        )
    
    # Create connection manager
    disconnect_delay = data.get(CONF_DELAY, DISCONNECT_DELAY)
    connection_mgr = ConnectionManager(hass, ble_device, disconnect_delay)
    
    # Create coordinator
    device_name = data.get("name", f"BLE Device {address}")
    coordinator = BLEDeviceCoordinator(
        hass, ble_device, device_name, connection_mgr
    )
    
    # Store coordinator in hass.data
    hass.data[DOMAIN][entry.entry_id] = coordinator
    
    # Start the coordinator (this begins listening for advertisements)
    entry.async_on_unload(coordinator.async_start())
    
    # Wait for device to be ready
    _LOGGER.info("[%s] Waiting for device to advertise...", address)
    if not await coordinator.async_wait_ready():
        raise ConfigEntryNotReady(
            f"Device {address} is not advertising. "
            "Please ensure the device is powered on and in range of a Bluetooth proxy."
        )
    
    _LOGGER.info("[%s] Device is ready", address)
    
    # Set up update listener for options changes
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    
    # Forward to platform setup
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        # Clean up coordinator
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        
        # Close connection manager
        if hasattr(coordinator, "connection_mgr"):
            await coordinator.connection_mgr.async_close()
        
        # Clean up domain data if no more entries
        if not hass.config_entries.async_entries(DOMAIN):
            hass.data.pop(DOMAIN)
    
    return unload_ok
