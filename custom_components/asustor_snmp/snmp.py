"""Bounded asynchronous, read-only SNMP access using PySNMP 7.1.27."""

import asyncio
from collections.abc import Mapping

from pyasn1.error import PyAsn1Error
from pyasn1.type.univ import Integer, OctetString
from pysnmp.error import PySnmpError
from pysnmp.hlapi.v3arch import asyncio as hlapi
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    Udp6TransportTarget,
    UdpTransportTarget,
    UsmUserData,
    bulk_cmd,
    get_cmd,
    next_cmd,
)
from pysnmp.proto import errind
from pysnmp.proto.rfc1905 import EndOfMibView, NoSuchInstance, NoSuchObject
from pysnmp.smi import view

from .const import DEFAULT_OPTIONS, IF_TABLE, IFX_TABLE, MODEL_OID, SERIAL_OID, SYS_UPTIME, VENDOR
from .telemetry import RawValue, text

MAX_WALK_VALUES = 4096
MAX_WALK_REQUESTS = 512
AUTH_PROTOCOLS = {
    "MD5": hlapi.USM_AUTH_HMAC96_MD5,
    "SHA": hlapi.USM_AUTH_HMAC96_SHA,
    "SHA256": hlapi.USM_AUTH_HMAC192_SHA256,
    "SHA512": hlapi.USM_AUTH_HMAC384_SHA512,
}
PRIV_PROTOCOLS = {"AES": hlapi.USM_PRIV_CFB128_AES, "DES": hlapi.USM_PRIV_CBC56_DES}


class SnmpError(Exception):
    """Safe-to-display communication or response error."""


class SnmpAuthError(SnmpError):
    """Credentials or security parameters were explicitly rejected."""


class SnmpAccessError(SnmpError):
    """An authenticated request is outside the user's permitted SNMP view."""


class NotAsustorError(SnmpError):
    """The mandatory ASUSTOR identity OIDs are absent."""


def _make_engine() -> SnmpEngine:
    """Load bundled MIBs in a worker thread, never on HA's event loop."""
    engine = SnmpEngine()
    controller = view.MibViewController(
        engine.message_dispatcher.mib_instrum_controller.get_mib_builder()
    )
    engine.cache["mibViewController"] = controller
    controller.mibBuilder.load_modules()
    return engine


def _authentication(config: Mapping):
    """Create credentials without ever placing secrets in exceptions or logs."""
    if config.get("version", "3") == "2c":
        return CommunityData(config["community"], mpModel=1)
    level = config.get("security_level", "authNoPriv")
    return UsmUserData(
        config["username"],
        authKey=config.get("auth_key") if level != "noAuthNoPriv" else None,
        authProtocol=AUTH_PROTOCOLS[config.get("auth_protocol", "MD5")]
        if level != "noAuthNoPriv"
        else hlapi.USM_AUTH_NONE,
        privKey=config.get("priv_key") if level == "authPriv" else None,
        privProtocol=PRIV_PROTOCOLS[config.get("priv_protocol", "AES")]
        if level == "authPriv"
        else hlapi.USM_PRIV_NONE,
    )


def _check_response(error, status) -> None:
    if error:
        if isinstance(
            error,
            (
                errind.WrongDigest,
                errind.UnknownUserName,
                errind.UnsupportedSecurityLevel,
                errind.DecryptionError,
                errind.AuthenticationFailure,
            ),
        ):
            raise SnmpAuthError("SNMP authentication or security settings were rejected")
        if isinstance(error, errind.RequestTimedOut):
            raise SnmpError("The NAS did not respond before the request deadline")
        raise SnmpError("SNMP request failed")
    if status:
        if int(status) in (6, 16):
            raise SnmpAccessError("SNMP access was denied by the configured view")
        raise SnmpError("The NAS rejected an SNMP request")


def _decode(value) -> RawValue | None:
    if isinstance(value, (NoSuchObject, NoSuchInstance, EndOfMibView)):
        return None
    if isinstance(value, Integer):
        return int(value)
    if isinstance(value, OctetString):
        return bytes(value.asOctets())
    return str(value)


class SnmpClient:
    """One isolated engine per NAS; polls serialize and have total deadlines."""

    def __init__(self, config: Mapping, options: Mapping) -> None:
        self._config = dict(config)
        self.options = DEFAULT_OPTIONS | dict(options)
        self._engine = None
        self._target = None
        self._auth = None
        self._lock = asyncio.Lock()
        self.network_available = False
        self._generation = 0

    async def async_open(self) -> None:
        if self._engine is not None:
            return
        generation = self._generation
        engine = await asyncio.to_thread(_make_engine)
        try:
            auth = _authentication(self._config)
            host = self._config["host"]
            target_class = Udp6TransportTarget if ":" in host else UdpTransportTarget
            target = await target_class.create(
                (host, self._config.get("port", 161)),
                timeout=self.options["timeout"],
                retries=self.options["retries"],
            )
        except PySnmpError, PyAsn1Error, OSError, ValueError, KeyError:
            engine.close_dispatcher()
            raise SnmpError("Unable to initialise SNMP; check host and security settings") from None
        except BaseException:
            engine.close_dispatcher()
            raise
        if generation != self._generation:
            engine.close_dispatcher()
            raise SnmpError("The SNMP connection was closed during initialisation")
        self._engine, self._auth, self._target = engine, auth, target

    def close(self) -> None:
        """Release sockets and timers; safe after partial setup or repeated calls."""
        self._generation += 1
        if self._engine is not None:
            self._engine.close_dispatcher()
        self._engine = self._target = self._auth = None

    def _args(self):
        return (
            self._engine,
            self._auth,
            self._target,
            ContextData(contextName=self._config.get("context_name", "")),
        )

    async def async_get(self, oids: list[str]) -> dict[str, RawValue]:
        try:
            error, status, _, pairs = await get_cmd(
                *self._args(), *(ObjectType(ObjectIdentity(oid)) for oid in oids), lookupMib=False
            )
            _check_response(error, status)
            return {
                str(oid): decoded for oid, value in pairs if (decoded := _decode(value)) is not None
            }
        except PySnmpError, PyAsn1Error:
            raise SnmpError("SNMP response could not be decoded") from None

    async def async_walk(self, root: str) -> dict[str, RawValue]:
        """GETBULK with a bounded GETNEXT fallback for agents rejecting bulk PDUs."""
        prefix = tuple(map(int, root.split(".")))
        cursor = prefix
        result: dict[str, RawValue] = {}
        bulk = True
        try:
            for _ in range(MAX_WALK_REQUESTS):
                bind = ObjectType(ObjectIdentity(cursor))
                if bulk:
                    response = await bulk_cmd(*self._args(), 0, 16, bind, lookupMib=False)
                else:
                    response = await next_cmd(*self._args(), bind, lookupMib=False)
                error, status, _, pairs = response
                if not error and int(status) in (1, 5) and bulk:
                    bulk = False
                    continue
                _check_response(error, status)
                if not pairs:
                    return result
                for oid, value in pairs:
                    numeric = tuple(oid)
                    if isinstance(value, (EndOfMibView, NoSuchObject, NoSuchInstance)):
                        return result
                    if numeric[: len(prefix)] != prefix:
                        return result
                    if numeric <= cursor:
                        raise SnmpError("The NAS returned a non-increasing OID during a walk")
                    cursor = numeric
                    if (decoded := _decode(value)) is not None:
                        result[str(oid)] = decoded
                    if len(result) > MAX_WALK_VALUES:
                        raise SnmpError("SNMP table exceeded the supported size limit")
            raise SnmpError("SNMP walk exceeded the request limit")
        except PySnmpError, PyAsn1Error:
            raise SnmpError("SNMP table could not be decoded") from None

    async def async_probe(self) -> dict[str, RawValue]:
        """Validate identity with two small scalar requests in a bounded window."""
        async with self._lock, asyncio.timeout(20):
            await self.async_open()
            raw = await self.async_get([SERIAL_OID, MODEL_OID])
            if not text(raw.get(SERIAL_OID)) or not text(raw.get(MODEL_OID)):
                raise NotAsustorError("The host did not return an ASUSTOR serial number and model")
            return raw

    async def async_fetch(self) -> dict[str, RawValue]:
        """Fetch one fresh snapshot; incomplete network results are discarded."""
        async with self._lock, asyncio.timeout(45):
            await self.async_open()
            async with asyncio.timeout(25):
                raw = await self.async_walk(VENDOR)
            if not text(raw.get(SERIAL_OID)) or not text(raw.get(MODEL_OID)):
                raise NotAsustorError("The host did not return an ASUSTOR serial number and model")
            self.network_available = False
            try:
                async with asyncio.timeout(15):
                    network = await self.async_walk(IF_TABLE)
                    network.update(await self.async_walk(IFX_TABLE))
                    network.update(await self.async_get([SYS_UPTIME]))
            except SnmpAuthError:
                raise
            except SnmpError, TimeoutError, OSError:
                # Drop outstanding transport retries after a partial-network timeout.
                self.close()
                return raw
            self.network_available = bool(network)
            raw.update(network)
            return raw
