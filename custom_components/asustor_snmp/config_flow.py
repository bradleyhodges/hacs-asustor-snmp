"""UI setup, reauthentication, reconfiguration and monitoring options."""

import ipaddress
import re
from collections.abc import Mapping

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import DEFAULT_OPTIONS, DOMAIN, SERIAL_OID
from .snmp import (
    AUTH_PROTOCOLS,
    PRIV_PROTOCOLS,
    NotAsustorError,
    SnmpAuthError,
    SnmpClient,
    SnmpError,
)
from .telemetry import text

SECRETS = {"auth_key", "priv_key", "community"}
PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def connection_schema(defaults: Mapping) -> vol.Schema:
    """Do not put stored credentials in form defaults."""

    def optional(key, default):
        return vol.Optional(key, default=defaults.get(key, default))

    return vol.Schema(
        {
            vol.Required("name", default=defaults.get("name", "ASUSTOR NAS")): str,
            vol.Required("host", default=defaults.get("host", "")): str,
            optional("port", 161): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            optional("username", ""): str,
            vol.Optional("auth_key"): PASSWORD,
            vol.Required("advanced", default=dict): section(
                vol.Schema(
                    {
                        optional("version", "3"): vol.In(["3", "2c"]),
                        optional("security_level", "authNoPriv"): vol.In(
                            ["authNoPriv", "authPriv", "noAuthNoPriv"]
                        ),
                        optional("auth_protocol", "MD5"): vol.In(list(AUTH_PROTOCOLS)),
                        optional("priv_protocol", "AES"): vol.In(list(PRIV_PROTOCOLS)),
                        vol.Optional("priv_key"): PASSWORD,
                        vol.Optional("community"): PASSWORD,
                        optional("context_name", ""): str,
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


def validate_input(data: Mapping, old: Mapping | None = None) -> dict:
    """Validate security combinations and preserve blank secrets on reconfigure."""
    config = dict(data)
    for key in SECRETS:
        if not config.get(key) and old and old.get(key):
            config[key] = old[key]
    config["name"] = config.get("name", "ASUSTOR NAS").strip()
    host = config["host"].strip().strip("[]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}\.?$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?\.?", host):
            raise ValueError("invalid_host") from None
    config["host"] = host
    if not config["name"]:
        raise ValueError("invalid_config")
    if config.get("version", "3") == "2c":
        if not config.get("community"):
            raise ValueError("invalid_config")
        for key in ("username", "auth_key", "priv_key"):
            config.pop(key, None)
    else:
        if not config.get("username", "").strip():
            raise ValueError("invalid_config")
        level = config.get("security_level", "authNoPriv")
        if level != "noAuthNoPriv" and len(config.get("auth_key", "")) < 8:
            raise ValueError("invalid_config")
        if level == "authPriv" and len(config.get("priv_key", "")) < 8:
            raise ValueError("invalid_config")
        config.pop("community", None)
        if level == "noAuthNoPriv":
            config.pop("auth_key", None)
        if level != "authPriv":
            config.pop("priv_key", None)
    try:
        for key in ("username", "auth_key", "priv_key", "community", "context_name"):
            if key in config:
                encoded = config[key].encode("iso-8859-1")
                if key in {"username", "context_name"} and len(encoded) > 32:
                    raise ValueError("invalid_config")
    except UnicodeEncodeError:
        raise ValueError("invalid_config") from None
    return config


class AsustorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Identify NAS devices by their serial, never their network address."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        return await self._async_configure("user", user_input)

    async def async_step_reconfigure(self, user_input=None):
        return await self._async_configure("reconfigure", user_input)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._async_configure("reauth_confirm", user_input)

    async def _async_configure(self, step, user_input):
        entry = None
        if step == "reconfigure":
            entry = self._get_reconfigure_entry()
        elif step == "reauth_confirm":
            entry = self._get_reauth_entry()
        defaults = dict(entry.data) if entry else {}
        errors = {}
        if user_input is not None:
            # Sections nest form input; keep the existing flat config entry format.
            user_input = dict(user_input)
            user_input.update(user_input.pop("advanced", {}))
            defaults.update({k: v for k, v in user_input.items() if k not in SECRETS})
            client = None
            try:
                config = validate_input(user_input, entry.data if entry else None)
                client = SnmpClient(config, entry.options if entry else {})
                raw = await client.async_probe()
                serial = text(raw.get(SERIAL_OID))
                await self.async_set_unique_id(serial)
                if entry:
                    if serial != entry.unique_id:
                        return self.async_abort(reason="wrong_device")
                    return self.async_update_reload_and_abort(
                        entry, data=config, title=config["name"]
                    )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=config["name"], data=config)
            except SnmpAuthError:
                errors["base"] = "invalid_auth"
            except NotAsustorError:
                errors["base"] = "not_asustor"
            except SnmpError, TimeoutError, OSError:
                errors["base"] = "cannot_connect"
            except ValueError as exc:
                errors["base"] = (
                    str(exc) if str(exc) in {"invalid_host", "invalid_config"} else "invalid_config"
                )
            finally:
                if client:
                    client.close()
        return self.async_show_form(
            step_id=step, data_schema=connection_schema(defaults), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return AsustorOptionsFlow()


class AsustorOptionsFlow(config_entries.OptionsFlowWithReload):
    """Reload polling and entity discovery when monitoring options change."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = DEFAULT_OPTIONS | dict(self.config_entry.options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("scan_interval", default=current["scan_interval"]): vol.All(
                        vol.Coerce(int), vol.Range(min=15, max=3600)
                    ),
                    vol.Required("timeout", default=current["timeout"]): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=10)
                    ),
                    vol.Required("retries", default=current["retries"]): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=3)
                    ),
                    vol.Required("include_virtual", default=current["include_virtual"]): bool,
                    vol.Required("enable_ups", default=current["enable_ups"]): bool,
                    vol.Required("storage_unit", default=current["storage_unit"]): vol.In(
                        ["GiB", "GB"]
                    ),
                    vol.Required("memory_unit", default=current["memory_unit"]): vol.In(
                        ["MiB", "MB"]
                    ),
                }
            ),
        )
