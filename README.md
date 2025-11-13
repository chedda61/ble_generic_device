# BLE Generic Device

Custom Home Assistant integration to control a BLE device locally
or via proxies.

## What works

- Adding devices via MAC and service UUID
- Adding/Removing switches via characteristic UUID
- Setting connection timeout (connection stays open for set amount after
  a switch was toggled)
- Switches become unavailable when no advertisement for 6 minutes
  (can't be less due to HA caching)
- Switches become unavailable when device not reachable
- Switches become available with first advertisement
  BUT sometimes they won't -> Reload integration

## Installation

Add this repository as a custom integration:
1. In HACS, go to **Integrations → Custom repositories**
2. Paste this URL: https://github.com/chedda61/ble_generic_device
   Category: **Integration**

3. Click **Add**, then search for "BLE Generic Device" and install.


Restart Home Assistant.

