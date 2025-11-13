"""Coordinator for BLE Generic Device integration."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import HomeAssistant, callback
from bleak.backends.device import BLEDevice

from .connection_manager import ConnectionManager

_LOGGER = logging.getLogger(__name__)

DEVICE_STARTUP_TIMEOUT = 30  # seconds
UNAVAILABLE_TIMEOUT = 45  # seconds - mark unavailable after this many seconds without advertisement


class BLEDeviceCoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Coordinator to manage BLE device connection and availability."""

    def __init__(
        self,
        hass: HomeAssistant,
        ble_device: BLEDevice,
        device_name: str,
        connection_mgr: ConnectionManager,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            address=ble_device.address,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_update,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            connectable=True,
        )
        self.ble_device = ble_device
        self.device_name = device_name
        self.connection_mgr = connection_mgr
        self._ready_event = asyncio.Event()
        self._last_seen: datetime | None = None
        self._manually_marked_unavailable = False  # Track manual unavailability from write failure
        
        # Override the unavailable timeout
        self.unavailable_track_seconds = UNAVAILABLE_TIMEOUT
        
        _LOGGER.info(
            "[%s] Coordinator initialized with %ds unavailability timeout",
            ble_device.address,
            UNAVAILABLE_TIMEOUT,
        )

    @property
    def available(self) -> bool:
        """Return if the device is available."""
        # If actively connected, always available
        if self.connection_mgr.is_connected():
            if self._manually_marked_unavailable:
                _LOGGER.debug(
                    "[%s] Available (connected), clearing manual unavailable flag",
                    self.ble_device.address,
                )
                self._manually_marked_unavailable = False
            return True
        
        # If manually marked unavailable due to write failure, stay unavailable
        # until we receive a new advertisement
        if self._manually_marked_unavailable:
            _LOGGER.debug(
                "[%s] Unavailable (manually marked after write failure)",
                self.ble_device.address,
            )
            return False
        
        # Otherwise use parent's availability logic (based on advertisements)
        parent_available = super().available
        
        if self._last_seen:
            age = (datetime.utcnow() - self._last_seen).total_seconds()
            _LOGGER.debug(
                "[%s] Availability: %s (last_seen %.1fs ago, threshold %ds)",
                self.ble_device.address,
                parent_available,
                age,
                UNAVAILABLE_TIMEOUT,
            )
        
        return parent_available

    def mark_write_failed(self):
        """Mark that a write operation failed - makes coordinator unavailable."""
        _LOGGER.warning(
            "[%s] Write operation failed, marking unavailable until next advertisement",
            self.ble_device.address,
        )
        self._manually_marked_unavailable = True
        
        # Trigger update for all listeners
        self.async_update_listeners()

    @callback
    def _needs_poll(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        """Determine if we need to poll the device."""
        return False

    async def _async_update(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Poll the device (not used in our case)."""
        pass

    @callback
    def _async_handle_unavailable(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Handle the device going unavailable."""
        time_since_last_seen = None
        if self._last_seen:
            time_since_last_seen = (datetime.utcnow() - self._last_seen).total_seconds()
        
        _LOGGER.warning(
            "[%s] Device became UNAVAILABLE via coordinator (last seen %.1fs ago)",
            self.ble_device.address,
            time_since_last_seen if time_since_last_seen else 0,
        )
        
        # Call parent
        super()._async_handle_unavailable(service_info)

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event (advertisement received)."""
        # Update BLE device reference
        self.ble_device = service_info.device
        self._last_seen = datetime.utcnow()
        
        proxy_name = getattr(service_info, "source", "unknown")
        rssi = getattr(service_info, "rssi", "unknown")
        
        # If was manually marked unavailable, clear it now that we got an advertisement
        was_manually_unavailable = self._manually_marked_unavailable
        
        if was_manually_unavailable:
            _LOGGER.info(
                "[%s] Device recovered - advertisement received via proxy '%s' (RSSI: %s)",
                self.ble_device.address,
                proxy_name,
                rssi,
            )
            self._manually_marked_unavailable = False
        else:
            _LOGGER.debug(
                "[%s] Advertisement via proxy '%s' (RSSI: %s, change: %s)",
                self.ble_device.address,
                proxy_name,
                rssi,
                change,
            )
        
        # Mark as ready
        self._ready_event.set()
        
        # If we just recovered from manual unavailability, force an extra update
        if was_manually_unavailable:
            self.async_update_listeners()

            # Schedule a task to read all characteristics after recovery
            self.hass.async_create_task(self._async_refresh_all_states())

            _LOGGER.info(
                "[%s] Forced entity updates after recovery",
                self.ble_device.address,
            )

    async def async_wait_ready(self) -> bool:
        """Wait for the device to be ready."""
        with contextlib.suppress(asyncio.TimeoutError):
            async with asyncio.timeout(DEVICE_STARTUP_TIMEOUT):
                await self._ready_event.wait()
                return True
        return False
