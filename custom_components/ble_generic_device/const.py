DOMAIN = "ble_generic_device"

CONF_MAC = "mac_address"
CONF_SERVICE = "service_uuid"
CONF_CHARS = "characteristics"

CONF_NAME = "name"
CONF_MANUFACTURER = "manufacturer"
CONF_DELAY = "disconnect_delay"

DEVICE_STARTUP_TIMEOUT_SECONDS = 30
# delay before releasing the connection
DISCONNECT_DELAY = 15 


class BLEDeviceNotAvailable(Exception):
    """Exception raised when BLE device is not available for connection."""
    pass
