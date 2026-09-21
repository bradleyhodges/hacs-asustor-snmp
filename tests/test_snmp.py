"""Transport boundary and failure behaviour, independent of a physical NAS."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from pysnmp.proto import errind
from pysnmp.proto.rfc1902 import Integer32, ObjectName, OctetString
from pysnmp.proto.rfc1905 import endOfMibView, noSuchInstance

from custom_components.asustor_snmp.const import MODEL_OID, SERIAL_OID, VENDOR
from custom_components.asustor_snmp.snmp import SnmpAuthError, SnmpClient, SnmpError


def client():
    c = SnmpClient({"host": "127.0.0.1", "username": "test", "auth_key": "testing123"}, {})
    c._engine = Mock()
    c._target = Mock()
    c._auth = Mock()
    return c


def response(*pairs, error=None, status=0):
    return error, status, 0, [(ObjectName(oid), value) for oid, value in pairs]


async def test_walk_stops_at_subtree_boundary():
    c = client()
    with patch(
        "custom_components.asustor_snmp.snmp.bulk_cmd",
        new=AsyncMock(
            return_value=response(
                (SERIAL_OID, OctetString("test")), ("1.3.6.1.4.1.50000.0", Integer32(1))
            )
        ),
    ):
        assert await c.async_walk(VENDOR) == {SERIAL_OID: b"test"}


async def test_non_increasing_walk_is_rejected():
    c = client()
    with patch(
        "custom_components.asustor_snmp.snmp.bulk_cmd",
        new=AsyncMock(
            return_value=response(
                (SERIAL_OID, OctetString("test")), (SERIAL_OID, OctetString("test"))
            )
        ),
    ):
        with pytest.raises(SnmpError, match="increasing"):
            await c.async_walk(VENDOR)


async def test_missing_oid_is_not_a_value():
    c = client()
    with patch(
        "custom_components.asustor_snmp.snmp.get_cmd",
        new=AsyncMock(return_value=response((SERIAL_OID, noSuchInstance))),
    ):
        assert await c.async_get([SERIAL_OID]) == {}


async def test_auth_error_is_separate_from_connection_error():
    c = client()
    with patch(
        "custom_components.asustor_snmp.snmp.get_cmd",
        new=AsyncMock(return_value=response(error=errind.wrongDigest)),
    ):
        with pytest.raises(SnmpAuthError):
            await c.async_get([SERIAL_OID])


async def test_timeout_has_safe_error_message():
    c = client()
    with patch(
        "custom_components.asustor_snmp.snmp.get_cmd",
        new=AsyncMock(return_value=response(error=errind.requestTimedOut)),
    ):
        with pytest.raises(SnmpError, match="respond"):
            await c.async_get([SERIAL_OID])


async def test_walk_limit_is_not_silent_truncation():
    c = client()
    with (
        patch("custom_components.asustor_snmp.snmp.MAX_WALK_VALUES", 1),
        patch(
            "custom_components.asustor_snmp.snmp.bulk_cmd",
            new=AsyncMock(
                return_value=response(
                    (SERIAL_OID, OctetString("test")), (MODEL_OID, OctetString("AS3A02T"))
                )
            ),
        ),
    ):
        with pytest.raises(SnmpError, match="limit"):
            await c.async_walk(VENDOR)


async def test_empty_walk_terminates():
    c = client()
    with patch(
        "custom_components.asustor_snmp.snmp.bulk_cmd",
        new=AsyncMock(return_value=response((VENDOR + ".0", endOfMibView))),
    ):
        assert await c.async_walk(VENDOR) == {}


async def test_optional_network_failure_preserves_vendor_data():
    c = client()
    c.async_open = AsyncMock()
    c.async_walk = AsyncMock(
        side_effect=[{SERIAL_OID: b"test", MODEL_OID: b"model"}, SnmpError("offline")]
    )
    c.async_get = AsyncMock(return_value={})
    result = await c.async_fetch()
    assert result[SERIAL_OID] == b"test"
    assert c.network_available is False


async def test_probe_rejects_non_asustor():
    c = client()
    c.async_open = AsyncMock()
    c.async_get = AsyncMock(return_value={})
    with pytest.raises(SnmpError, match="ASUSTOR"):
        await c.async_probe()


async def test_close_during_open_does_not_reopen_transport():
    import asyncio

    started, release = asyncio.Event(), asyncio.Event()
    engine = Mock()

    async def delayed_target(*args, **kwargs):
        started.set()
        await release.wait()
        return Mock()

    c = SnmpClient({"host": "127.0.0.1", "username": "test", "auth_key": "testing123"}, {})
    with (
        patch("custom_components.asustor_snmp.snmp._make_engine", return_value=engine),
        patch(
            "custom_components.asustor_snmp.snmp.UdpTransportTarget.create",
            side_effect=delayed_target,
        ),
    ):
        task = asyncio.create_task(c.async_open())
        await started.wait()
        c.close()
        release.set()
        outcome = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcome[0], SnmpError)
        assert c._engine is None
        engine.close_dispatcher.assert_called_once()


async def test_asn1_error_is_sanitized():
    from pyasn1.error import PyAsn1Error

    c = client()
    with patch(
        "custom_components.asustor_snmp.snmp.get_cmd",
        new=AsyncMock(side_effect=PyAsn1Error("potential-secret")),
    ):
        with pytest.raises(SnmpError) as result:
            await c.async_get([SERIAL_OID])
        assert "potential-secret" not in str(result.value)


@pytest.mark.parametrize("status", [6, 16])
async def test_view_denial_is_not_an_authentication_failure(status):
    c = client()
    with patch(
        "custom_components.asustor_snmp.snmp.get_cmd",
        new=AsyncMock(return_value=response(status=status)),
    ):
        with pytest.raises(SnmpError) as result:
            await c.async_get([SERIAL_OID])
        assert not isinstance(result.value, SnmpAuthError)


async def test_view_denial_does_not_hide_vendor_telemetry():
    from custom_components.asustor_snmp.snmp import _check_response

    c = client()
    c.async_open = AsyncMock()
    count = 0

    async def walk(root):
        nonlocal count
        count += 1
        if count == 1:
            return {SERIAL_OID: b"test", MODEL_OID: b"AS3A02T"}
        _check_response(None, 16)

    c.async_walk = walk
    assert (await c.async_fetch())[SERIAL_OID] == b"test"
