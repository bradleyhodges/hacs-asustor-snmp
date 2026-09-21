"""Integration constants and documented numeric OIDs."""

DOMAIN = "asustor_snmp"
VERSION = "1.0.0"
VENDOR = "1.3.6.1.4.1.44738"
IF_TABLE = "1.3.6.1.2.1.2.2.1"
IFX_TABLE = "1.3.6.1.2.1.31.1.1.1"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SERIAL_OID = f"{VENDOR}.1.1.0"
MODEL_OID = f"{VENDOR}.2.1.0"
DEFAULT_OPTIONS = {
    "scan_interval": 30,
    "timeout": 3,
    "retries": 1,
    "include_virtual": False,
    "enable_ups": False,
    "storage_unit": "GiB",
    "memory_unit": "MiB",
}
