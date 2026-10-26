"""Config flow tests."""

from __future__ import annotations

from http import HTTPStatus

import aiohttp
import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_MAC, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.teslapi.config_flow import InvalidHost, parse_host
from custom_components.teslapi.const import DOMAIN

from .conftest import HOST, HOST2, TOKEN, make_entry, mock_pi


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_without_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An open Pi needs only a host."""
    mock_pi(aioclient_mock)
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": f"http://{HOST}/", "port": 80}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "TeslaPi (teslapi)"
    assert result["data"] == {"host": HOST, "port": 80}
    assert result["result"].unique_id == f"{HOST}:80"
    assert result["result"].version == 2


async def test_user_with_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A password is checked by logging in and stored in data."""
    mock_pi(aioclient_mock)
    aioclient_mock.post(
        f"http://{HOST}:80/api/auth/login",
        json={"ok": True},
        cookies={"teslapi_session": TOKEN},
    )
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST, "port": 80, CONF_PASSWORD: "hunter2"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASSWORD] == "hunter2"

    status_calls = [c for c in aioclient_mock.mock_calls if c[1].path == "/api/status"]
    assert status_calls[-1][3]["Cookie"] == f"teslapi_session={TOKEN}"


async def test_user_invalid_auth_wrong_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A rejected password shows invalid_auth."""
    mock_pi(aioclient_mock)
    aioclient_mock.post(f"http://{HOST}:80/api/auth/login", status=HTTPStatus.UNAUTHORIZED)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST, "port": 80, CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_invalid_auth_password_needed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A Pi with a password set and none given shows invalid_auth."""
    mock_pi(aioclient_mock, status=HTTPStatus.UNAUTHORIZED)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST, "port": 80}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No answer from /api/health is cannot_connect."""
    aioclient_mock.get(
        f"http://{HOST}:80/api/health", exc=aiohttp.ClientConnectionError()
    )
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": HOST, "port": 80}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_invalid_host(hass: HomeAssistant) -> None:
    """Garbage in the host field is rejected before any request."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "not a host", "port": 80}
    )
    assert result["errors"] == {"host": "invalid_host"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.0.0.5", ("10.0.0.5", None)),
        ("http://teslapi.local/", ("teslapi.local", None)),
        ("teslapi:8080", ("teslapi", 8080)),
        ("[fd00::5]:80", ("fd00::5", 80)),
    ],
)
def test_parse_host(raw: str, expected: tuple[str, int | None]) -> None:
    """Pasted URLs and host:port are cleaned up."""
    assert parse_host(raw) == expected


@pytest.mark.parametrize("raw", ["", "a b", "host:99999", "host:abc", "a:b:c"])
def test_parse_host_rejects(raw: str) -> None:
    """Bad input raises InvalidHost."""
    with pytest.raises(InvalidHost):
        parse_host(raw)


async def test_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Reauth stores the new password."""
    entry = make_entry(password="old")
    entry.add_to_hass(hass)
    mock_pi(aioclient_mock)
    aioclient_mock.post(
        f"http://{HOST}:80/api/auth/login",
        json={"ok": True},
        cookies={"teslapi_session": TOKEN},
    )

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"


async def test_reauth_invalid_auth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Reauth keeps the form open on a wrong password."""
    entry = make_entry(password="old")
    entry.add_to_hass(hass)
    mock_pi(aioclient_mock)
    aioclient_mock.post(f"http://{HOST}:80/api/auth/login", status=HTTPStatus.UNAUTHORIZED)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "still wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "old"


async def test_dhcp_moves_known_pi(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A Pi seen before by MAC is moved to its new IP, not added twice."""
    mock_pi(aioclient_mock, host=HOST2)
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_MAC: "aa:bb:cc:dd:ee:ff"}
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_DHCP},
        data=DhcpServiceInfo(ip=HOST2, hostname="teslapi", macaddress="aabbccddeeff"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == HOST2
    assert entry.unique_id == f"{HOST2}:80"
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_options_fallback_hosts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Fallback hosts are cleaned, de-duplicated and validated."""
    entry = make_entry()
    entry.add_to_hass(hass)
    mock_pi(aioclient_mock)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval": 30, "extra_hosts": ["teslapi.local", "bad host"]},
    )
    assert result["errors"] == {"extra_hosts": "invalid_host"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "scan_interval": 30,
            "extra_hosts": ["http://teslapi.local/", HOST, "teslapi.local", ""],
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["extra_hosts"] == ["teslapi.local"]
