"""Compatible connection manager for any HA Bluetooth version."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.helpers.event import async_call_later
from homeassistant.components import bluetooth
from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import (
    establish_connection,
    BleakConnectionError,
    BleakNotFoundError,
)

from .const import DISCONNECT_DELAY, BLEDeviceNotAvailable

_LOGGER = logging.getLogger(__name__)


class ConnectionManager:
    """Keeps BLE device connection open briefly after last command."""

    def __init__(self, hass, ble_device, delay):
        """Initialize the connection manager."""
        self.hass = hass
        self._ble_device = ble_device
        self._delay = delay
        self._lock = asyncio.Lock()
        self._disconnect_handle = None
        self._client = None

    def is_connected(self) -> bool:
        """Return True if currently connected."""
        return self._client is not None and self._client.is_connected

    async def _resolve_device(self):
        """Ensure we have a valid BLEDevice object."""
        # If already a proper BLEDevice, return it
        if hasattr(self._ble_device, "details"):
            return self._ble_device
        
        # Otherwise try to resolve from address
        address = (
            self._ble_device 
            if isinstance(self._ble_device, str) 
            else getattr(self._ble_device, "address", None)
        )
        
        if not address:
            raise BLEDeviceNotAvailable("Cannot determine BLE device address")
        
        ble_dev = bluetooth.async_ble_device_from_address(
            self.hass, address, connectable=True
        )
        
        if ble_dev is None:
            raise BLEDeviceNotAvailable(
                f"BLE device {address} not currently visible to any proxy"
            )
        
        _LOGGER.debug("[%s] Resolved BLEDevice", address)
        self._ble_device = ble_dev
        return ble_dev

    async def _ensure_client(self):
        """Ensure there is a connected Bleak client."""
        # Resolve device if needed
        device = await self._resolve_device()
    
        # Reuse if already connected
        if self._client and self._client.is_connected:
            return self._client
    
        # Clean up old client if exists but not connected
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
    
        # Establish new connection
        _LOGGER.debug("[%s] Establishing connection", device.address)
        try:
            self._client = await establish_connection(
                BleakClient,
                device,
                device.name or device.address,
                use_services_cache=True,
            )
    
            # Ensure services are discovered
            if not self._client.services:
                _LOGGER.debug("[%s] Services not cached, discovering...", device.address)
                await self._client.get_services()
    
        except (BleakConnectionError, BleakNotFoundError, TimeoutError) as err:
            _LOGGER.warning(
                "[%s] Failed to establish connection: %s",
                device.address,
                err,
            )
            raise BLEDeviceNotAvailable(
                f"Could not connect to {device.address}"
            ) from err
    
        _LOGGER.debug("[%s] Connected", device.address)
        return self._client

    async def write(self, char_uuid: str, value: bytes):
        """Write value to characteristic and refresh linger timer."""
        async with self._lock:
            device_addr = getattr(self._ble_device, "address", str(self._ble_device))
            
            try:
                # First, try using HA's high-level write function (faster)
                write_func = getattr(bluetooth, "async_write_characteristic", None)
                
                if callable(write_func):
                    _LOGGER.debug(
                        "[%s] Writing %s to %s (HA method)",
                        device_addr,
                        value.hex(),
                        char_uuid,
                    )
                    try:
                        device = await self._resolve_device()
                        # Add timeout to write operation
                        async with asyncio.timeout(5):  # 5 second timeout
                            await write_func(self.hass, device, char_uuid, value)
                    except (BleakError, TimeoutError, asyncio.TimeoutError) as err:
                        _LOGGER.warning(
                            "[%s] HA write method failed: %s, trying direct connection",
                            device_addr,
                            err,
                        )
                        # Fall back to direct connection
                        async with asyncio.timeout(10):  # 10 second timeout for connection + write
                            client = await self._ensure_client()
                            await client.write_gatt_char(char_uuid, value, response=True)
                else:
                    # Use direct Bleak connection
                    _LOGGER.debug(
                        "[%s] Writing %s to %s (Bleak method)",
                        device_addr,
                        value.hex(),
                        char_uuid,
                    )
                    async with asyncio.timeout(10):  # 10 second timeout
                        client = await self._ensure_client()
                        await client.write_gatt_char(char_uuid, value, response=True)
                
                _LOGGER.debug(
                    "[%s] Successfully wrote to %s",
                    device_addr,
                    char_uuid,
                )
                
            except BLEDeviceNotAvailable:
                # Re-raise our custom exception
                raise
            except (asyncio.TimeoutError, TimeoutError) as err:
                _LOGGER.error(
                    "[%s] Write timeout to %s (device likely offline)",
                    device_addr,
                    char_uuid,
                )
                # Clean up failed client
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None
                
                raise BLEDeviceNotAvailable(
                    f"Device {device_addr} write timeout (likely offline)"
                ) from err
            except (BleakError, BleakConnectionError, BleakNotFoundError) as err:
                _LOGGER.error(
                    "[%s] Write failed to %s: %s (%s)",
                    device_addr,
                    char_uuid,
                    err,
                    type(err).__name__,
                )
                # Clean up failed client
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None
                
                # Convert to our exception
                raise BLEDeviceNotAvailable(
                    f"Device {device_addr} not available for write operation"
                ) from err
            except Exception as err:
                _LOGGER.error(
                    "[%s] Unexpected error writing to %s: %s (%s)",
                    device_addr,
                    char_uuid,
                    err,
                    type(err).__name__,
                )
                # Clean up
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception:
                        pass
                    self._client = None
                raise
            
            finally:
                # Always extend connection timer (even on failure)
                self._extend_connection()

    def _extend_connection(self):
        """Extend the connection linger timer."""
        # Cancel existing timer
        if self._disconnect_handle:
            self._disconnect_handle()
            self._disconnect_handle = None
        
        # Only schedule disconnect if we have a client
        if self._client:
            self._disconnect_handle = async_call_later(
                self.hass, self._delay, self._async_disconnect
            )
            _LOGGER.debug(
                "[%s] Extended connection timer (%s seconds)",
                getattr(self._ble_device, "address", "unknown"),
                self._delay,
            )

    async def _async_disconnect(self, *_):
        """Disconnect from device after idle period."""
        device_addr = getattr(self._ble_device, "address", "unknown")
        
        # Clear the handle first
        if self._disconnect_handle:
            self._disconnect_handle()
            self._disconnect_handle = None
        
        # Disconnect if still connected
        if self._client and self._client.is_connected:
            try:
                _LOGGER.debug(
                    "[%s] Disconnecting after %s seconds idle",
                    device_addr,
                    self._delay,
                )
                await self._client.disconnect()
            except Exception as err:
                _LOGGER.warning(
                    "[%s] Error during disconnect: %s",
                    device_addr,
                    err,
                )
            finally:
                self._client = None

    async def async_close(self):
        """Clean up connection and timers."""
        device_addr = getattr(self._ble_device, "address", "unknown")
        
        # Cancel disconnect timer
        if self._disconnect_handle:
            self._disconnect_handle()
            self._disconnect_handle = None
        
        # Disconnect client
        if self._client:
            try:
                if self._client.is_connected:
                    _LOGGER.debug("[%s] Closing connection", device_addr)
                    await self._client.disconnect()
            except Exception as err:
                _LOGGER.warning("[%s] Error closing connection: %s", device_addr, err)
            finally:
                self._client = None
