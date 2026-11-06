"""Tests for the config flow."""

import asyncio
from collections.abc import Generator
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest

from custom_components.bru_tea_maker import pairing
from custom_components.bru_tea_maker.const import DOMAIN
from custom_components.bru_tea_maker.pairing import BruPairingError, record_failure
from homeassistant.components.bluetooth import BaseHaRemoteScanner
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ADDRESS, NAME, ble_device, service_info

FLOW = "custom_components.bru_tea_maker.config_flow"


@pytest.fixture(autouse=True)
def bluetooth(enable_bluetooth: None) -> Generator[None]:
    """Show the tea maker as reachable through the local adapter."""
    pairing._failures.clear()
    with (
        patch(f"{FLOW}.async_ble_device_from_address", return_value=ble_device()),
        patch(f"{FLOW}.async_discovered_service_info", return_value=[service_info()]),
        patch(f"{FLOW}.async_scanner_devices_by_address", return_value=[]),
        patch("custom_components.bru_tea_maker.async_setup_entry", return_value=True),
    ):
        yield
    pairing._failures.clear()


class FakePairing:
    """A pairing attempt the test drives."""

    instances: ClassVar[list[FakePairing]] = []

    def __init__(self, _ble_device: Any) -> None:
        loop = asyncio.get_running_loop()
        self.passkey: asyncio.Future[int] = loop.create_future()
        self.result: asyncio.Future[None] = loop.create_future()
        self.confirmed = False
        FakePairing.instances.append(self)

    def confirm(self) -> None:
        self.confirmed = True

    async def async_run(self) -> None:
        await self.result


@pytest.fixture
def fake_pairing() -> Generator[type[FakePairing]]:
    """Replace the pairing with one the test controls."""
    FakePairing.instances = []
    with patch(f"{FLOW}.BruPairing", FakePairing):
        yield FakePairing


async def _settle(hass: HomeAssistant) -> None:
    """Let the flow's tasks run, without waiting for an unfinished pairing."""
    for _ in range(10):
        await asyncio.sleep(0)


async def _progress_done(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    """Finish a progress step and return the step it leads to."""
    await _settle(hass)
    result = await hass.config_entries.flow.async_configure(flow_id)
    if result["type"] is FlowResultType.SHOW_PROGRESS_DONE:
        result = await hass.config_entries.flow.async_configure(flow_id)
    return dict(result)


async def _start_user_flow(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: ADDRESS}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "pair"
    return dict(result)


async def test_local_adapter_shows_code(
    hass: HomeAssistant, fake_pairing: type[FakePairing]
) -> None:
    """On a local adapter the code is shown as six digits, like on the display."""
    result = await _start_user_flow(hass)
    attempt = fake_pairing.instances[0]
    attempt.passkey.set_result(42)
    result = await _progress_done(hass, result["flow_id"])
    assert result["step_id"] == "confirm_code"
    assert result["description_placeholders"] == {"code": "000042"}

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))
    assert attempt.confirmed
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    attempt.result.set_result(None)
    result = await _progress_done(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: ADDRESS}


async def test_code_format(
    hass: HomeAssistant, fake_pairing: type[FakePairing]
) -> None:
    """A six-digit code is shown as it is."""
    result = await _start_user_flow(hass)
    fake_pairing.instances[0].passkey.set_result(123456)
    result = await _progress_done(hass, result["flow_id"])
    assert result["description_placeholders"]["code"] == "123456"
    fake_pairing.instances[0].result.set_result(None)


async def test_progress_names_the_tea_maker(
    hass: HomeAssistant, fake_pairing: type[FakePairing]
) -> None:
    """The progress text, which is all a proxy user sees, names the tea maker."""
    result = await _start_user_flow(hass)
    assert result["description_placeholders"] == {"name": NAME}
    fake_pairing.instances[0].result.set_result(None)
    await _settle(hass)


async def test_proxy_path_without_code(
    hass: HomeAssistant, fake_pairing: type[FakePairing]
) -> None:
    """Without a code the pairing just ends and the entry is created."""
    result = await _start_user_flow(hass)
    fake_pairing.instances[0].result.set_result(None)
    result = await _progress_done(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_bluetooth_discovery(
    hass: HomeAssistant, fake_pairing: type[FakePairing]
) -> None:
    """A discovered tea maker is confirmed, then paired."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    fake_pairing.instances[0].result.set_result(None)
    result = await _progress_done(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME


async def test_no_devices(hass: HomeAssistant) -> None:
    """Without tea makers in range the flow aborts."""
    with patch(f"{FLOW}.async_discovered_service_info", return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (BruPairingError("pair_timeout"), "pair_timeout"),
        (BruPairingError("pair_rejected"), "pair_rejected"),
        (BruPairingError("cannot_connect"), "cannot_connect"),
        (BruPairingError("no_connection_slots"), "no_connection_slots"),
        (RuntimeError("unexpected"), "pairing_failed"),
    ],
)
async def test_failure_offers_retry(
    hass: HomeAssistant,
    fake_pairing: type[FakePairing],
    error: Exception,
    reason: str,
) -> None:
    """A failure says what went wrong and pairing can be retried."""
    result = await _start_user_flow(hass)
    fake_pairing.instances[0].result.set_exception(error)
    result = await _progress_done(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair_failed"
    assert result["errors"] == {"base": reason}

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert len(fake_pairing.instances) == 2
    fake_pairing.instances[1].result.set_result(None)
    result = await _progress_done(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_lockout_hint_does_not_block(
    hass: HomeAssistant, fake_pairing: type[FakePairing]
) -> None:
    """After a failure the wait is suggested once; Submit pairs right away."""
    with patch.object(pairing.time, "monotonic", return_value=1000.0):
        record_failure(ADDRESS)
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "locked_out"
        assert result["description_placeholders"] == {"minutes": "5"}
        assert not fake_pairing.instances

        # Still within the suggested wait: Submit pairs anyway.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    fake_pairing.instances[0].result.set_result(None)
    await _settle(hass)


async def test_reauth(
    hass: HomeAssistant,
    fake_pairing: type[FakePairing],
    config_entry: MockConfigEntry,
) -> None:
    """Pairing again updates and reloads the existing entry."""
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    fake_pairing.instances[0].result.set_exception(BruPairingError("pair_timeout"))
    result = await _progress_done(hass, result["flow_id"])
    assert result["step_id"] == "pair_failed"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    # The fake pairing records no failure, so there is no lockout to wait out.
    assert result["step_id"] == "pair"
    fake_pairing.instances[1].result.set_result(None)
    result = await _progress_done(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"


def _proxy(name: str) -> MagicMock:
    scanner = MagicMock(spec=BaseHaRemoteScanner)
    scanner.name = name
    device = MagicMock()
    device.scanner = scanner
    return device


async def test_proxy_info(hass: HomeAssistant, fake_pairing: type[FakePairing]) -> None:
    """Through a proxy the flow explains the proxy setup before pairing."""
    with patch(
        f"{FLOW}.async_scanner_devices_by_address",
        return_value=[_proxy("kitchen-proxy"), _proxy("kitchen-proxy")],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "proxy_info"
        placeholders = result["description_placeholders"]
        assert placeholders["proxy"] == "kitchen-proxy"
        assert placeholders["address"] == ADDRESS

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "pair"
        fake_pairing.instances[0].result.set_exception(BruPairingError("pair_rejected"))
        result = await _progress_done(hass, result["flow_id"])
        assert result["errors"] == {"base": "pair_rejected_proxy"}

        # The retry goes straight to pairing; the proxy info is shown once.
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "pair"
        fake_pairing.instances[1].result.set_result(None)
        await _settle(hass)
