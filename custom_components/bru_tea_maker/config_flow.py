"""Config flow for the BRU Tea Maker integration."""

import asyncio
from collections.abc import Mapping
import math
from typing import Any

import probatio as vol

from homeassistant.components.bluetooth import (
    BaseHaRemoteScanner,
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
    async_scanner_devices_by_address,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import DOMAIN, LOGGER
from .pairing import BruPairing, BruPairingError, lockout_remaining

ENTRY_TITLE = "BRU Tea Maker"
_DOCS_URL = "https://github.com/girtskokars/ha-bru-tea-maker#esphome-bluetooth-proxy"

# Failures a misconfigured proxy causes; through a proxy they get a hint.
_PROXY_HINT_REASONS = {"pair_timeout", "pair_rejected"}


class BruConfigFlow(ConfigFlow, domain=DOMAIN):
    """Find a BRU tea maker and pair with it."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}
        self._address = ""
        self._name = ""
        self._proxy_info_shown = False
        self._lockout_hint_shown = False
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
            return await self.async_step_proxy_info()
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
            return await self.async_step_proxy_info()

        configured = self._async_current_ids()
        self._discovered = {
            info.address: info.name
            for info in async_discovered_service_info(self.hass, connectable=True)
            if info.name.startswith("BRU.M") and info.address not in configured
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
            return await self.async_step_proxy_info()
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"name": self._name},
        )

    async def async_step_proxy_info(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain the proxy setup when the tea maker is reached through one."""
        proxies = [] if self._proxy_info_shown else self._proxy_names()
        if user_input is not None or not proxies:
            return await self.async_step_pair()
        self._proxy_info_shown = True
        return self.async_show_form(
            step_id="proxy_info",
            description_placeholders={
                "name": self._name,
                "proxy": ", ".join(proxies),
                "address": self._address,
                "docs_url": _DOCS_URL,
            },
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair, until a code needs confirming here or the pairing ends."""
        if self._pair_task is None:
            if not self._lockout_hint_shown and (
                remaining := lockout_remaining(self._address)
            ):
                # Only a hint: whether, and how long, the tea maker refuses
                # attempts after a failure is not known for sure.
                self._lockout_hint_shown = True
                return self.async_show_form(
                    step_id="locked_out",
                    description_placeholders={
                        "minutes": str(math.ceil(remaining / 60))
                    },
                )
            ble_device = async_ble_device_from_address(
                self.hass, self._address, connectable=True
            )
            if ble_device is None:
                return self.async_abort(reason="cannot_connect")
            self._pairing = pairing = BruPairing(ble_device)
            # Background tasks: HA cancels them quietly when it stops.
            self._pair_task = pair_task = self.hass.async_create_background_task(
                pairing.async_run(), f"{DOMAIN} pairing {self._address}"
            )
            self._code_task = self.hass.async_create_background_task(
                self._async_wait_for_code(pair_task, pairing),
                f"{DOMAIN} pairing code {self._address}",
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

    async def async_step_locked_out(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Try again, whether or not the user waited."""
        return await self.async_step_pair()

    async def async_step_confirm_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the code and confirm Home Assistant's side of the comparison."""
        assert self._pairing is not None
        if user_input is None:
            passkey = self._pairing.passkey.result()
            return self.async_show_form(
                step_id="confirm_code",
                # Six digits in one block, as the tea maker shows it.
                description_placeholders={"code": f"{passkey:06d}"},
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
        """Create the entry, reload it after pairing again, or offer a retry."""
        task = self._pair_task
        assert task is not None
        err = task.exception() if not task.cancelled() else None
        if err is None and not task.cancelled():
            if self.source == SOURCE_REAUTH:
                return self.async_update_reload_and_abort(self._get_reauth_entry())
            # The Bluetooth name is shown while choosing the tea maker; the
            # entry is named for what it is, and renamed in the UI.
            return self.async_create_entry(
                title=ENTRY_TITLE, data={CONF_ADDRESS: self._address}
            )
        LOGGER.debug(
            "Pairing with %s failed: %r (cause: %r)",
            self._address,
            err,
            err.__cause__ if err else None,
        )
        reason = err.reason if isinstance(err, BruPairingError) else "pairing_failed"
        if reason in _PROXY_HINT_REASONS and self._proxy_names():
            reason = f"{reason}_proxy"
        self._pairing = self._pair_task = self._code_task = None
        self._lockout_hint_shown = False
        return self.async_show_form(
            step_id="pair_failed",
            errors={"base": reason},
            description_placeholders={"docs_url": _DOCS_URL},
        )

    async def async_step_pair_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Try pairing again after a failure."""
        return await self.async_step_pair()

    @callback
    def async_remove(self) -> None:
        """Stop a pairing still in progress when the flow is closed."""
        if self._pair_task is not None:
            self._pair_task.cancel()

    @staticmethod
    async def _async_wait_for_code(
        pair_task: asyncio.Task[None], pairing: BruPairing
    ) -> None:
        """Return once the code needs confirming or the pairing has ended."""
        waiting: set[asyncio.Future[Any]] = {pair_task, pairing.passkey}
        await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

    def _proxy_names(self) -> list[str]:
        """Return the names of Bluetooth proxies that can reach the tea maker."""
        return sorted(
            {
                device.scanner.name
                for device in async_scanner_devices_by_address(
                    self.hass, self._address, connectable=True
                )
                if isinstance(device.scanner, BaseHaRemoteScanner)
            }
        )
