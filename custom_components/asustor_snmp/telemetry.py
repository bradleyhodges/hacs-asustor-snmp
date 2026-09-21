"""Normalize ASUSTOR and IF-MIB data without depending on Home Assistant.

Mappings follow the three supplied ASUSTOR MIBs. A missing or invalid value
is None, never zero. Unknown textual health states are preserved as text and
produce an unknown problem sensor. Sizes retain the explicitly selected unit.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from .const import DEFAULT_OPTIONS, IF_TABLE, IFX_TABLE, MODEL_OID, SERIAL_OID, SYS_UPTIME, VENDOR

type RawValue = int | str | bytes
type RawData = Mapping[str, RawValue]


@dataclass(frozen=True, slots=True)
class Reading:
    """One entity value plus stable presentation metadata."""

    key: str
    name: str
    value: str | int | float | bool | None
    platform: str = "sensor"
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    diagnostic: bool = False
    enabled: bool = True
    attributes: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NetworkSample:
    """Counter baseline; deliberately not restored after an HA restart."""

    at: float
    index: int
    mac: str | None
    uptime: int | None
    discontinuity: int | None
    rx: int | None
    tx: int | None
    speed: int | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A complete polling result, including fresh rate baselines."""

    serial: str
    model: str | None
    version: str | None
    readings: dict[str, Reading]
    network: dict[str, NetworkSample]


def text(value: RawValue | None) -> str | None:
    """Decode display text, dropping empty responses and NUL padding."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if value is None:
        return None
    return str(value).replace("\x00", "").strip()[:255] or None


def number(value: RawValue | None, low: int = 0, high: int | None = None) -> int | None:
    """Accept integral readings within the specified physical range."""
    try:
        result = int(value) if value is not None else None
    except TypeError, ValueError, OverflowError:
        return None
    if result is None or result < low or (high is not None and result > high):
        return None
    return result


def table(raw: RawData, root: str) -> dict[int, dict[int, RawValue]]:
    """Parse column/index tables; never assume consecutive or one-based indices."""
    rows: dict[int, dict[int, RawValue]] = {}
    prefix = root + "."
    for oid, value in raw.items():
        if oid.startswith(prefix):
            parts = oid[len(prefix) :].split(".")
            if len(parts) == 2 and all(p.isdecimal() for p in parts):
                column, index = map(int, parts)
                rows.setdefault(index, {})[column] = value
    return rows


def problem(*states: RawValue | None) -> bool | None:
    """Recognize documented/observed healthy states and explicit fault states."""
    normalized = [text(s).casefold() if text(s) else "" for s in states]
    if any(
        s
        in {
            "bad",
            "failed",
            "failing",
            "failure",
            "degraded",
            "critical",
            "error",
            "abnormal",
            "crashed",
            "warning",
            "unhealthy",
        }
        for s in normalized
    ):
        return True
    if normalized and all(s in {"good", "healthy", "normal", "ok"} for s in normalized):
        return False
    return None


def update_available(value: RawValue | None) -> bool | None:
    """Do not interpret arbitrary/localized ADM sentences as Boolean values."""
    value = (text(value) or "").casefold().rstrip(".")
    if value in {"available", "there is a new version ready for download"}:
        return True
    if value in {"unavailable", "this adm is latest version", "the adm is latest version"}:
        return False
    return None


def mac_address(value: RawValue | None) -> str | None:
    """Support IF-MIB binary addresses and ASUSTOR textual MAC addresses."""
    if isinstance(value, bytes) and len(value) == 6:
        return ":".join(f"{b:02x}" for b in value)
    value = (text(value) or "").lower()
    return value if re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", value) else None


def _rate(
    current: NetworkSample, old: NetworkSample | None, direction: str, max_gap: float
) -> float | None:
    """Calculate Mbit/s only across continuous, physically credible samples."""
    if old is None:
        return None
    elapsed = current.at - old.at
    a, b = getattr(current, direction), getattr(old, direction)
    if (
        elapsed <= 0
        or elapsed > max_gap
        or a is None
        or b is None
        or a < b
        or current.index != old.index
        or current.mac != old.mac
        or current.discontinuity != old.discontinuity
        or current.uptime is None
        or old.uptime is None
        or current.uptime < old.uptime
    ):
        return None
    rate = (a - b) * 8 / elapsed / 1_000_000
    # Allow small timing/skew variation; an impossible rate resets the baseline.
    if current.speed and rate > current.speed * 1.10:
        return None
    return rate


def build_snapshot(
    raw: RawData,
    *,
    now: float,
    previous: Mapping[str, NetworkSample] | None = None,
    options: Mapping | None = None,
) -> Snapshot:
    """Build a snapshot from this poll only; never carry stale values forward."""
    opts = DEFAULT_OPTIONS | dict(options or {})
    readings: dict[str, Reading] = {}
    network: dict[str, NetworkSample] = {}

    def add(key, name, value, **kwargs):
        readings[key] = Reading(key, name, value, **kwargs)

    def measure(key, name, value, unit, device_class=None, **kwargs):
        add(
            key,
            name,
            value,
            unit=unit,
            device_class=device_class,
            state_class="measurement",
            **kwargs,
        )

    def binary(key, name, value, device_class="problem", **kwargs):
        add(key, name, value, platform="binary_sensor", device_class=device_class, **kwargs)

    def sizes(prefix, name, total_value, free_value, unit):
        total, free = number(total_value, 1), number(free_value)
        valid = total is not None and free is not None and free <= total
        used = total - free if valid else None
        for suffix, value in (("total", total), ("free", free if valid else None), ("used", used)):
            measure(
                f"{prefix}_{suffix}",
                f"{name} {suffix}",
                value,
                unit,
                "data_size",
                diagnostic=suffix == "total",
                enabled=suffix != "total",
            )
        measure(f"{prefix}_usage", f"{name} usage", used / total * 100 if valid else None, "%")

    for suffix, key, name in (
        (2, "adm_version", "ADM version"),
        (3, "bios_version", "BIOS version"),
        (4, "uptime", "Uptime"),
        (6, "timezone", "Time zone"),
        (7, "adm_status", "ADM update status"),
    ):
        if (oid := f"{VENDOR}.1.{suffix}.0") in raw:
            add(key, name, text(raw[oid]), diagnostic=True, enabled=key != "timezone")
    if (oid := f"{VENDOR}.1.7.0") in raw:
        binary(
            "adm_update",
            "ADM update available",
            update_available(raw[oid]),
            "update",
            diagnostic=True,
        )

    cores = table(raw, f"{VENDOR}.2.3.1")
    loads = [number(row.get(2), 0, 100) for row in cores.values()]
    if cores:
        valid_loads = [load for load in loads if load is not None]
        measure(
            "cpu_usage",
            "CPU usage",
            sum(valid_loads) / len(valid_loads) if len(valid_loads) == len(loads) else None,
            "%",
        )
    for index, row in cores.items():
        measure(
            f"cpu_{index}_usage",
            f"CPU {index} usage",
            number(row.get(2), 0, 100),
            "%",
            enabled=False,
            diagnostic=True,
        )
    for index, row in table(raw, f"{VENDOR}.2.4.1").items():
        measure(f"fan_{index}_speed", f"Fan {index} speed", number(row.get(2), 0, 100000), "rpm")
    for index, row in table(raw, f"{VENDOR}.2.5.1").items():
        for column, label in ((2, "CPU"), (3, "System")):
            if column in row:
                measure(
                    f"temperature_{index}_{label.lower()}",
                    f"{label} temperature {index}",
                    number(row[column], -40, 150),
                    "°C",
                    "temperature",
                )
    for index, row in table(raw, f"{VENDOR}.2.6.1").items():
        sizes(f"memory_{index}", f"Memory {index}", row.get(2), row.get(3), opts["memory_unit"])
        if 4 in row:
            measure(
                f"memory_{index}_free_reported",
                f"Memory {index} reported free",
                number(row[4], 0, 100),
                "%",
                diagnostic=True,
                enabled=False,
            )

    for index, row in table(raw, f"{VENDOR}.4.1.1").items():
        prefix, name = f"disk_{index}", text(row.get(2)) or f"Disk bay {index}"
        attrs = {"bay_index": index, "model": text(row.get(3)), "interface": text(row.get(4))}
        add(f"{prefix}_status", f"{name} status", text(row.get(5)), attributes=attrs)
        add(f"{prefix}_smart", f"{name} SMART status", text(row.get(6)))
        binary(f"{prefix}_problem", f"{name} problem", problem(row.get(5), row.get(6)))
        measure(
            f"{prefix}_temperature",
            f"{name} temperature",
            number(row.get(7), -40, 150),
            "°C",
            "temperature",
        )
        measure(
            f"{prefix}_size",
            f"{name} capacity",
            number(row.get(8), 1),
            opts["storage_unit"],
            "data_size",
            diagnostic=True,
            enabled=False,
        )

    for index, row in table(raw, f"{VENDOR}.5.1.1").items():
        prefix, name = f"volume_{index}", text(row.get(2)) or f"Volume {index}"
        add(
            f"{prefix}_status",
            f"{name} status",
            text(row.get(4)),
            attributes={"raid_level": text(row.get(3)), "filesystem": text(row.get(5))},
        )
        binary(f"{prefix}_problem", f"{name} problem", problem(row.get(4)))
        sizes(prefix, name, row.get(6), row.get(7), opts["storage_unit"])

    base, extended = table(raw, IF_TABLE), table(raw, IFX_TABLE)
    uptime = number(raw.get(SYS_UPTIME))
    names_seen: set[str] = set()
    for index, row in base.items():
        ext = extended.get(index, {})
        name = text(ext.get(1)) or text(row.get(2))
        if not name or name in names_seen:
            continue
        names_seen.add(name)
        # Physical Ethernet names and ASUSTOR LAN labels are enabled by default.
        physical = bool(re.fullmatch(r"(?:eth\d+|en[opsx][\w]+|LAN\d+)", name, re.IGNORECASE))
        if not physical and not opts["include_virtual"]:
            continue
        key = f"net_{name.encode().hex()}"
        speed = number(ext.get(15))
        if speed is None and (bps := number(row.get(5))) is not None:
            speed = bps // 1_000_000
        current = NetworkSample(
            now,
            index,
            mac_address(row.get(6)),
            uptime,
            number(ext.get(19)),
            number(ext.get(6)),
            number(ext.get(10)),
            speed,
        )
        network[key] = current
        old = (previous or {}).get(key)
        oper = number(row.get(8), 1, 7)
        binary(
            f"{key}_link",
            f"{name} link",
            oper == 1 if oper is not None and oper != 4 else None,
            "connectivity",
            attributes={"interface": name, "if_index": index},
        )
        measure(
            f"{key}_speed",
            f"{name} link speed",
            speed,
            "Mbit/s",
            "data_rate",
            diagnostic=True,
            enabled=False,
        )
        for direction, label in (("rx", "received"), ("tx", "sent")):
            count = getattr(current, direction)
            if (6 if direction == "rx" else 10) not in ext:
                continue
            measure(
                f"{key}_{direction}_rate",
                f"{name} {label} rate",
                _rate(current, old, direction, max(120, opts["scan_interval"] * 3)),
                "Mbit/s",
                "data_rate",
            )
            add(
                f"{key}_{direction}_total",
                f"{name} total {label}",
                count,
                unit="B",
                device_class="data_size",
                enabled=False,
                diagnostic=True,
            )
        for column, label in (
            (14, "rx_errors"),
            (20, "tx_errors"),
            (13, "rx_discards"),
            (19, "tx_discards"),
        ):
            if column in row:
                # Counter32 resets/wraps: leave these as diagnostic point readings.
                add(
                    f"{key}_{label}",
                    f"{name} {label.replace('_', ' ')}",
                    number(row[column]),
                    diagnostic=True,
                    enabled=False,
                )

    if opts["enable_ups"]:
        for index, row in table(raw, f"{VENDOR}.6.1.1").items():
            if not (text(row.get(2)) or text(row.get(3))):
                continue
            prefix, name = f"ups_{index}", f"UPS {index}"
            status = text(row.get(7))
            tokens = set((status or "").upper().split())
            add(
                f"{prefix}_status",
                f"{name} status",
                status,
                attributes={"manufacturer": text(row.get(2)), "model": text(row.get(3))},
            )
            binary(
                f"{prefix}_on_battery",
                f"{name} on battery",
                True
                if "OB" in tokens and "OL" not in tokens
                else False
                if "OL" in tokens and "OB" not in tokens
                else None,
                "power",
            )
            binary(
                f"{prefix}_low_battery",
                f"{name} low battery",
                "LB" in tokens if tokens & {"OL", "OB"} else None,
                "battery",
            )
            measure(
                f"{prefix}_charge",
                f"{name} battery charge",
                number(row.get(9), 0, 100),
                "%",
                "battery",
            )
            measure(
                f"{prefix}_runtime",
                f"{name} remaining runtime",
                number(row.get(8)),
                "s",
                "duration",
            )
            measure(
                f"{prefix}_voltage",
                f"{name} input voltage",
                number(row.get(11), 0, 1000),
                "V",
                "voltage",
            )
    return Snapshot(
        text(raw.get(SERIAL_OID)) or "",
        text(raw.get(MODEL_OID)),
        text(raw.get(f"{VENDOR}.1.2.0")),
        readings,
        network,
    )
