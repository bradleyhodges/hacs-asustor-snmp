"""Real localhost UDP exchanges: no mocked SNMP client or request functions."""

import asyncio
from bisect import bisect_right

import pytest
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.entity import config
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.proto.rfc1902 import Counter64, Integer32, ObjectName, OctetString
from pysnmp.proto.rfc1905 import endOfMibView, noSuchInstance

from custom_components.asustor_snmp.const import IFX_TABLE, MODEL_OID, SERIAL_OID
from custom_components.asustor_snmp.snmp import SnmpClient, SnmpError, _make_engine
from custom_components.asustor_snmp.telemetry import build_snapshot


class TestInstrumentation:
    """A fixed, sorted MIB view served by a real PySNMP protocol engine."""

    __test__ = False

    def __init__(self, raw):
        self.values = {}
        for oid, value in raw.items():
            key = tuple(map(int, oid.split(".")))
            if isinstance(value, int):
                value = Counter64(value) if oid.startswith(IFX_TABLE + ".") else Integer32(value)
            else:
                value = OctetString(value)
            self.values[key] = value
        self.keys = sorted(self.values)

    def read_variables(self, *var_binds, **kwargs):
        return [(oid, self.values.get(tuple(oid), noSuchInstance)) for oid, _ in var_binds]

    def read_next_variables(self, *var_binds, **kwargs):
        result = []
        for oid, _ in var_binds:
            i = bisect_right(self.keys, tuple(oid))
            key = self.keys[i] if i < len(self.keys) else tuple(oid)
            result.append(
                (ObjectName(key), self.values[key] if i < len(self.keys) else endOfMibView)
            )
        return result


@pytest.fixture
async def agent(raw, socket_enabled):
    engine = await asyncio.to_thread(_make_engine)
    transport = udp.UdpTransport().open_server_mode(("127.0.0.1", 0))
    config.add_transport(engine, udp.DOMAIN_NAME, transport)
    config.add_v3_user(engine, "test", config.USM_AUTH_HMAC96_MD5, "testing123")
    config.add_v3_user(
        engine,
        "private",
        config.USM_AUTH_HMAC96_SHA,
        "testing123",
        config.USM_PRIV_CFB128_AES,
        "private123",
    )
    config.add_v1_system(engine, "v2-test", "testing-community")
    ctx = context.SnmpContext(engine)
    ctx.unregister_context_name(b"")
    ctx.register_context_name(b"", TestInstrumentation(raw))
    responders = [
        cmdrsp.GetCommandResponder(engine, ctx),
        cmdrsp.NextCommandResponder(engine, ctx),
        cmdrsp.BulkCommandResponder(engine, ctx),
    ]
    await transport._lport
    port = transport.transport.get_extra_info("sockname")[1]
    try:
        yield port
    finally:
        for responder in responders:
            responder.close(engine)
        engine.close_dispatcher()
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    "credentials",
    [
        {
            "username": "test",
            "auth_key": "testing123",
            "auth_protocol": "MD5",
            "security_level": "authNoPriv",
        },
        {
            "username": "private",
            "auth_key": "testing123",
            "auth_protocol": "SHA",
            "security_level": "authPriv",
            "priv_key": "private123",
            "priv_protocol": "AES",
        },
        {"version": "2c", "community": "testing-community"},
    ],
)
async def test_live_udp_probe_and_full_poll(agent, credentials):
    client = SnmpClient(
        {"host": "127.0.0.1", "port": agent, **credentials}, {"timeout": 1, "retries": 0}
    )
    try:
        identity = await client.async_probe()
        assert identity[SERIAL_OID] == b"TEST-NAS-001"
        assert identity[MODEL_OID] == b"AS3A02T"
        raw = await client.async_fetch()
        result = build_snapshot(raw, now=1)
        assert result.readings["cpu_usage"].value == 1.75
        assert result.readings["net_65746830_rx_total"].value == 1000
        assert result.readings["disk_1_status"].value == "Good"
        assert client.network_available
    finally:
        client.close()
    assert client._engine is None


async def test_live_udp_bad_credentials_do_not_connect(agent):
    client = SnmpClient(
        {"host": "127.0.0.1", "port": agent, "username": "test", "auth_key": "wrong-password"},
        {"timeout": 1, "retries": 0},
    )
    try:
        with pytest.raises(SnmpError):
            await client.async_probe()
    finally:
        client.close()


async def test_real_ha_setup_poll_and_stop_use_event_loop(hass, agent):
    import threading
    from unittest.mock import patch

    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.asustor_snmp.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Protocol NAS",
        unique_id="TEST-NAS-001",
        data={"host": "127.0.0.1", "port": agent, "username": "test", "auth_key": "testing123"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "TEST-NAS-001_cpu_usage")
    assert hass.states.get(entity_id).state == "1.75"
    client = entry.runtime_data.client
    close = client.close
    thread_ids = []

    def observed_close():
        thread_ids.append(threading.get_ident())
        close()

    with patch.object(client, "close", side_effect=observed_close):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
    assert thread_ids and all(i == threading.get_ident() for i in thread_ids)
    assert client._engine is None
    assert await hass.config_entries.async_unload(entry.entry_id)
