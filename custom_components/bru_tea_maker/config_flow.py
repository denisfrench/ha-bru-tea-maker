"""Config flow for the BRU Tea Maker integration."""

import asyncio
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import DOMAIN, LOGGER
from .pairing import BruPairing


class BruConfigFlow(ConfigFlow, domain=DOMAIN):
    """Find a BRU tea maker and pair with it."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}
        self._address = ""
        self._name = ""
        self._pairing: BruPairing | None = None
        self._pair_task: asyncio.Task[None] | None = None
        self._code_task: asyncio.Task[None] | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a tea maker found by Bluetooth discovery."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self._name = discovery_info.name
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setting up the discovered tea maker."""
        if user_input is not None:
            return await self.async_step_pair()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a tea maker from the ones in range."""
        if user_input is not None:
            self._address = user_input[CONF_ADDRESS]
            self._name = self._discovered[self._address]
            await self.async_set_unique_id(self._address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return await self.async_step_pair()

        configured = self._async_current_ids()
        self._discovered = {
            info.address: info.name
            for info in async_discovered_service_info(self.hass, connectable=True)
            if info.name.startswith("BRU") and info.address not in configured
        }
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{name} ({address})"
                            for address, name in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Pair again after the bond turned out to be missing."""
        self._address = entry_data[CONF_ADDRESS]
        self._name = self._get_reauth_entry().title
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the tea maker to be put in pairing mode."""
        if user_input is not None:
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"name": self._name},
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair, until a code needs confirming here or the pairing ends."""
        if self._pair_task is None:
            ble_device = async_ble_device_from_address(
                self.hass, self._address, connectable=True
            )
            if ble_device is None:
                return self.async_abort(reason="cannot_connect")
            self._pairing = pairing = BruPairing(ble_device)
            self._pair_task = pair_task = self.hass.async_create_task(
                pairing.async_run()
            )
            self._code_task = self.hass.async_create_task(
                asyncio.wait(
                    {pair_task, pairing.passkey}, return_when=asyncio.FIRST_COMPLETED
                )
            )
        assert self._code_task is not None
        if not self._code_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="pairing",
                progress_task=self._code_task,
                description_placeholders={"name": self._name},
            )
        # Only a local adapter hands over the code. Anything else confirms its
        # side on its own, and the pairing simply ends.
        if self._pair_task.done():
            return self.async_show_progress_done(next_step_id="pair_done")
        return self.async_show_progress_done(next_step_id="confirm_code")

    async def async_step_confirm_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the code and confirm Home Assistant's side of the comparison."""
        assert self._pairing is not None
        if user_input is None:
            return self.async_show_form(
                step_id="confirm_code",
                description_placeholders={
                    "code": f"{self._pairing.passkey.result():06d}"
                },
            )
        self._pairing.confirm()
        return await self.async_step_pair_finish()

    async def async_step_pair_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Wait for the code to be accepted on the tea maker."""
        assert self._pair_task is not None
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair_finish",
                progress_action="waiting_for_device",
                progress_task=self._pair_task,
                description_placeholders={"name": self._name},
            )
        return self.async_show_progress_done(next_step_id="pair_done")

    async def async_step_pair_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry, or reload it after pairing again."""
        assert self._pair_task is not None
        if self._pair_task.cancelled():
            return self.async_abort(reason="pairing_failed")
        if err := self._pair_task.exception():
            LOGGER.debug("Pairing with %s failed: %r", self._address, err)
            return self.async_abort(reason="pairing_failed")
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(self._get_reauth_entry())
        return self.async_create_entry(
            title=self._name, data={CONF_ADDRESS: self._address}
        )

    @callback
    def async_remove(self) -> None:
        """Stop a pairing still in progress when the flow is closed."""
        if self._pair_task is not None:
            self._pair_task.cancel()
