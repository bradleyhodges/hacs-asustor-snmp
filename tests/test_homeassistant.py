"""Exercise UI flows, lifecycle, and entity state in Home Assistant 2026.9.2."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.config_validation import custom_serializer
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.asustor_snmp.const import DOMAIN, MODEL_OID, SERIAL_OID, VENDOR
from custom_components.asustor_snmp.snmp import SnmpAuthError, SnmpError

CONFIG = {
    "name": "Test NAS",
    "host": "192.0.2.1",
    "port": 161,
    "version": "3",
    "username": "test",
    "auth_key": "testpass123",
    "auth_protocol": "MD5",
    "security_level": "authNoPriv",
    "priv_protocol": "AES",
    "context_name": "",
}
ADVANCED_FIELDS = {
    "version",
    "security_level",
    "auth_protocol",
    "priv_protocol",
    "priv_key",
    "community",
    "context_name",
}


def form_input(config):
    """Submit the nested UI payload while stored entries remain flat."""
    return {key: value for key, value in config.items() if key not in ADVANCED_FIELDS} | {
        "advanced": {key: value for key, value in config.items() if key in ADVANCED_FIELDS}
    }


@pytest.mark.parametrize("source", [SOURCE_USER, "reconfigure", "reauth"])
async def test_connection_form_groups_advanced_fields(hass, source):
    entry = MockConfigEntry(domain=DOMAIN, data=CONFIG, unique_id="TEST-NAS-001")
    entry.add_to_hass(hass)
    form = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": source, "entry_id": entry.entry_id},
        data=CONFIG if source == "reauth" else None,
    )
    schema = form["data_schema"].schema
    assert set(schema) == {"name", "host", "port", "username", "auth_key", "advanced"}
    advanced = custom_serializer(schema["advanced"])
    assert advanced["type"] == "expandable"
    assert advanced["expanded"] is False
    assert {field["name"] for field in advanced["schema"]} == ADVANCED_FIELDS


async def test_ui_setup_validates_identity(hass):
    with (
        patch(
            "custom_components.asustor_snmp.config_flow.SnmpClient.async_probe",
            new=AsyncMock(return_value={SERIAL_OID: b"TEST-NAS-001", MODEL_OID: b"AS3A02T"}),
        ),
        patch("custom_components.asustor_snmp.async_setup_entry", return_value=True),
    ):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert form["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(form["flow_id"], form_input(CONFIG))
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == "TEST-NAS-001"
        assert result["data"]["auth_key"] == "testpass123"
        assert result["data"] == CONFIG


@pytest.mark.parametrize(
    "credentials, expected",
    [
        ({}, CONFIG),
        (
            {"advanced": {"version": "2c", "community": "testing-community"}},
            {key: value for key, value in CONFIG.items() if key not in {"username", "auth_key"}}
            | {"version": "2c", "community": "testing-community"},
        ),
        (
            {
                "advanced": {
                    "security_level": "authPriv",
                    "auth_protocol": "SHA",
                    "priv_key": "private123",
                    "context_name": "nas-context",
                }
            },
            CONFIG
            | {
                "security_level": "authPriv",
                "auth_protocol": "SHA",
                "priv_key": "private123",
                "context_name": "nas-context",
            },
        ),
    ],
    ids=["collapsed-defaults", "snmpv2c", "snmpv3-privacy"],
)
async def test_advanced_settings_are_saved_in_flat_entry(hass, credentials, expected):
    with (
        patch("custom_components.asustor_snmp.config_flow.SnmpClient") as client,
        patch("custom_components.asustor_snmp.async_setup_entry", return_value=True),
    ):
        client.return_value.async_probe = AsyncMock(return_value={SERIAL_OID: b"TEST-NAS-001"})
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        basic = {key: CONFIG[key] for key in ("name", "host", "username", "auth_key")}
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], basic | credentials
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"] == expected
        client.assert_called_once_with(expected, {})


@pytest.mark.parametrize(
    "error, code",
    [(SnmpAuthError("rejected"), "invalid_auth"), (SnmpError("offline"), "cannot_connect")],
)
async def test_flow_shows_actionable_error(hass, error, code):
    with patch(
        "custom_components.asustor_snmp.config_flow.SnmpClient.async_probe",
        new=AsyncMock(side_effect=error),
    ):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(form["flow_id"], form_input(CONFIG))
        assert result["errors"] == {"base": code}


async def test_error_form_preserves_advanced_choices_without_secrets(hass):
    config = CONFIG | {
        "security_level": "authPriv",
        "auth_protocol": "SHA",
        "priv_protocol": "DES",
        "priv_key": "private123",
        "context_name": "nas-context",
    }
    with patch(
        "custom_components.asustor_snmp.config_flow.SnmpClient.async_probe",
        new=AsyncMock(side_effect=SnmpError("offline")),
    ):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(form["flow_id"], form_input(config))
    assert result["errors"] == {"base": "cannot_connect"}
    assert result["data_schema"]({}) == form_input(
        {key: value for key, value in config.items() if key not in {"auth_key", "priv_key"}}
    )


async def test_duplicate_serial_is_rejected(hass):
    MockConfigEntry(domain=DOMAIN, data=CONFIG, unique_id="TEST-NAS-001").add_to_hass(hass)
    with patch(
        "custom_components.asustor_snmp.config_flow.SnmpClient.async_probe",
        new=AsyncMock(return_value={SERIAL_OID: b"TEST-NAS-001", MODEL_OID: b"AS3A02T"}),
    ):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(form["flow_id"], form_input(CONFIG))
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_entities_dynamic_addition_failure_recovery_and_unload(hass, raw):
    entry = MockConfigEntry(domain=DOMAIN, title="Test NAS", data=CONFIG, unique_id="TEST-NAS-001")
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.asustor_snmp.snmp.SnmpClient.async_fetch",
            new=AsyncMock(return_value=raw),
        ) as fetch,
        patch("custom_components.asustor_snmp.snmp.SnmpClient.close") as close,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        cpu_id = registry.async_get_entity_id("sensor", DOMAIN, "TEST-NAS-001_cpu_usage")
        assert hass.states.get(cpu_id).state == "1.75"
        coord = entry.runtime_data
        changed = dict(raw)
        changed[f"{VENDOR}.4.1.1.2.2"] = "Disk1"
        changed[f"{VENDOR}.4.1.1.7.2"] = 44
        fetch.return_value = changed
        await coord.async_refresh()
        await hass.async_block_till_done()
        disk_id = registry.async_get_entity_id("sensor", DOMAIN, "TEST-NAS-001_disk_2_temperature")
        assert hass.states.get(disk_id).state == "44"
        fetch.side_effect = SnmpError("offline")
        await coord.async_refresh()
        assert hass.states.get(cpu_id).state == STATE_UNAVAILABLE
        fetch.side_effect = None
        fetch.return_value = raw
        await coord.async_refresh()
        assert hass.states.get(cpu_id).state == "1.75"
        assert hass.states.get(disk_id).state == STATE_UNAVAILABLE
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert close.called


async def test_setup_failure_closes_transport(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Test NAS", data=CONFIG, unique_id="TEST-NAS-001")
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.asustor_snmp.snmp.SnmpClient.async_fetch",
            new=AsyncMock(side_effect=SnmpError("offline")),
        ),
        patch("custom_components.asustor_snmp.snmp.SnmpClient.close") as close,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        assert close.called


async def test_shutdown_cancels_an_inflight_poll_before_closing(hass):
    import asyncio

    from custom_components.asustor_snmp.coordinator import AsustorCoordinator

    entry = MockConfigEntry(domain=DOMAIN, title="Test NAS", data=CONFIG, unique_id="TEST-NAS-001")
    entry.add_to_hass(hass)
    coordinator = AsustorCoordinator(hass, entry)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def pending_fetch():
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    with (
        patch.object(coordinator.client, "async_fetch", side_effect=pending_fetch),
        patch.object(coordinator.client, "close") as close,
    ):
        task = asyncio.create_task(coordinator.async_refresh())
        try:
            await started.wait()
            await coordinator.async_shutdown()
            assert cancelled.is_set()
            assert close.called
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_reconfigure_preserves_blank_password_and_rejects_different_nas(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Test NAS", data=CONFIG, unique_id="TEST-NAS-001")
    entry.add_to_hass(hass)
    with patch(
        "custom_components.asustor_snmp.config_flow.SnmpClient.async_probe",
        new=AsyncMock(return_value={SERIAL_OID: b"ANOTHER-NAS", MODEL_OID: b"model"}),
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], form_input(CONFIG | {"auth_key": ""})
        )
        assert result["reason"] == "wrong_device"
        assert entry.data["auth_key"] == "testpass123"


@pytest.mark.parametrize(
    "credentials",
    [
        {"security_level": "authPriv", "priv_key": "private123"},
        {"version": "2c", "community": "testing-community"},
    ],
)
async def test_reconfigure_preserves_blank_advanced_secrets(hass, credentials):
    config = CONFIG | credentials
    if config["version"] == "2c":
        config.pop("username")
        config.pop("auth_key")
    entry = MockConfigEntry(domain=DOMAIN, data=config, unique_id="TEST-NAS-001")
    entry.add_to_hass(hass)
    with (
        patch("custom_components.asustor_snmp.config_flow.SnmpClient") as client,
        patch("homeassistant.config_entries.ConfigEntries.async_reload", return_value=True),
    ):
        client.return_value.async_probe = AsyncMock(return_value={SERIAL_OID: b"TEST-NAS-001"})
        form = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}
        )
        defaults = form["data_schema"]({})
        assert not {"priv_key", "community"} & defaults["advanced"].keys()
        assert "auth_key" not in defaults
        # Keep Advanced collapsed; its non-secret defaults must retain stored settings.
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], {"host": "192.0.2.2", "auth_key": ""}
        )
        assert result["reason"] == "reconfigure_successful"
        assert entry.data == config | {"host": "192.0.2.2"}
        client.assert_called_once_with(dict(entry.data), {})


async def test_reauthentication_updates_secret_and_reloads(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="Test NAS", data=CONFIG, unique_id="TEST-NAS-001")
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.asustor_snmp.config_flow.SnmpClient.async_probe",
            new=AsyncMock(return_value={SERIAL_OID: b"TEST-NAS-001", MODEL_OID: b"model"}),
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            new=AsyncMock(return_value=True),
        ) as reload,
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id}, data=CONFIG
        )
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], form_input(CONFIG | {"auth_key": "new-testing-password"})
        )
        await hass.async_block_till_done()
        assert result["reason"] == "reauth_successful"
        assert entry.data["auth_key"] == "new-testing-password"
        reload.assert_awaited_once()


async def test_options_update_and_reload(hass):
    from custom_components.asustor_snmp.const import DEFAULT_OPTIONS

    entry = MockConfigEntry(domain=DOMAIN, title="Test NAS", data=CONFIG, unique_id="TEST-NAS-001")
    entry.add_to_hass(hass)
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload", new=AsyncMock(return_value=True)
    ) as reload:
        form = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            form["flow_id"], DEFAULT_OPTIONS | {"scan_interval": 60}
        )
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options["scan_interval"] == 60
        reload.assert_awaited_once()


async def test_diagnostics_exclude_personal_data_and_vendor_strings(hass, raw):
    import json

    from custom_components.asustor_snmp.coordinator import AsustorCoordinator
    from custom_components.asustor_snmp.diagnostics import async_get_config_entry_diagnostics
    from custom_components.asustor_snmp.telemetry import build_snapshot

    entry = MockConfigEntry(
        domain=DOMAIN, title="Private Name", data=CONFIG, unique_id="TEST-NAS-001"
    )
    entry.add_to_hass(hass)
    coord = AsustorCoordinator(hass, entry)
    coord.async_set_updated_data(build_snapshot(raw, now=1))
    entry.runtime_data = coord
    result = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for secret in ("testpass123", "192.0.2.1", "TEST-NAS-001", "Private Name", "eth0", "ST8000VN"):
        assert secret not in result
    await coord.async_shutdown()


async def test_unsupported_credential_encoding_returns_form_error(hass):
    form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch("custom_components.asustor_snmp.config_flow.SnmpClient.async_probe") as probe:
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"], form_input(CONFIG | {"auth_key": "testing🔒password"})
        )
        assert result["errors"] == {"base": "invalid_config"}
        probe.assert_not_called()
