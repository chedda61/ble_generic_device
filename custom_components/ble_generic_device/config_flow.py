"""Config flow for BLE Generic Device integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    CONF_MAC,
    CONF_SERVICE,
    CONF_CHARS,
    DISCONNECT_DELAY,
    CONF_NAME,
    CONF_MANUFACTURER,
    CONF_DELAY,
)


class BLEGenericConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup flow."""
    
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            mac = user_input[CONF_MAC].upper()
            
            # Set unique ID based on MAC address to prevent duplicates
            await self.async_set_unique_id(mac.replace(":", "").lower())
            self._abort_if_unique_id_configured()
            
            # Start with empty list of characteristics
            user_input[CONF_CHARS] = []
            user_input.setdefault(CONF_DELAY, DISCONNECT_DELAY)
            user_input[CONF_MAC] = mac  # Store normalized MAC
            
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): str,
                vol.Required(CONF_MAC): str,
                vol.Required(CONF_SERVICE): str,
                vol.Optional(CONF_MANUFACTURER, default="Custom BLE"): str,
                vol.Optional(CONF_DELAY, default=DISCONNECT_DELAY): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        """Get the options flow for this handler."""
        return BLEGenericOptionsFlow(entry)


class BLEGenericOptionsFlow(config_entries.OptionsFlow):
    """Handle addition/removal of characteristics and editable delay."""

    def __init__(self, entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        self.entry = entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        # Read previously stored values (options override data)
        chars = list(
            self.entry.options.get(
                CONF_CHARS, self.entry.data.get(CONF_CHARS, [])
            )
        )
        current_delay = self.entry.options.get(
            CONF_DELAY, self.entry.data.get(CONF_DELAY, DISCONNECT_DELAY)
        )

        if user_input is not None:
            action = user_input.get("action")

            if action == "add":
                name = user_input.get("name")
                uuid = user_input.get("uuid")
                if name and uuid:
                    chars.append({"name": name, "uuid": uuid})

            elif action and action.startswith("remove_"):
                idx = int(action.split("_", 1)[1])
                if 0 <= idx < len(chars):
                    chars.pop(idx)

            # Save updated list and delay back to options
            return self.async_create_entry(
                title="",
                data={
                    CONF_CHARS: chars,
                    CONF_DELAY: user_input.get(CONF_DELAY, current_delay),
                },
            )

        # Build actions list
        actions = {"add": "Add new characteristic"}
        for i, ch in enumerate(chars):
            actions[f"remove_{i}"] = f"Remove {ch['name']}"

        schema = vol.Schema(
            {
                vol.Required("action", default="add"): vol.In(actions),
                vol.Optional("name"): str,
                vol.Optional("uuid"): str,
                vol.Optional(
                    CONF_DELAY,
                    default=current_delay,
                    description={"suggested_value": current_delay},
                ): int,
            }
        )
        
        description = (
            "Configured: " + ", ".join(c["name"] for c in chars)
            if chars
            else "No characteristics yet."
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"chars": description},
        )
