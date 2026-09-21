"""Regressions grounded in the supplied ASUSTOR MIBs and device readings."""

import pytest

from custom_components.asustor_snmp.telemetry import build_snapshot

V = "1.3.6.1.4.1.44738"
IF = "1.3.6.1.2.1.2.2.1"
IFX = "1.3.6.1.2.1.31.1.1.1"


@pytest.fixture
def raw():
    return {
        f"{V}.1.1.0": "TEST-NAS-001",
        f"{V}.1.2.0": "5.1.4.RL21",
        f"{V}.1.4.0": "8 min",
        f"{V}.1.7.0": "The ADM is latest version",
        f"{V}.2.1.0": "AS3A02T",
        **{f"{V}.2.3.1.2.{i}": v for i, v in enumerate([2, 2, 1, 2])},
        f"{V}.2.4.1.2.1": 1722,
        f"{V}.2.5.1.2.1": 38,
        f"{V}.2.5.1.3.1": -2147483648,
        f"{V}.2.6.1.2.1": 1828,
        f"{V}.2.6.1.3.1": 1160,
        f"{V}.2.6.1.4.1": 63,
        f"{V}.4.1.1.2.1": "Disk0",
        f"{V}.4.1.1.3.1": "ST8000VN004-3CP101",
        f"{V}.4.1.1.5.1": "Good",
        f"{V}.4.1.1.6.1": "Healthy",
        f"{V}.4.1.1.7.1": 46,
        f"{V}.4.1.1.8.1": 7452,
        f"{V}.5.1.1.2.1": "Volume1",
        f"{V}.5.1.1.3.1": "raid0",
        f"{V}.5.1.1.4.1": "healthy",
        f"{V}.5.1.1.5.1": "EXT4",
        f"{V}.5.1.1.6.1": 14776,
        f"{V}.5.1.1.7.1": 7299,
        f"{V}.6.1.1.2.1": "",
        f"{V}.6.1.1.3.1": "",
        f"{V}.6.1.1.7.1": "OB",
        f"{V}.6.1.1.9.1": 0,
        "1.3.6.1.2.1.1.3.0": 10000,
        f"{IF}.2.2": "eth0",
        f"{IF}.3.2": 6,
        f"{IF}.6.2": bytes.fromhex("001122334455"),
        f"{IF}.8.2": 1,
        f"{IFX}.1.2": "eth0",
        f"{IFX}.6.2": 1000,
        f"{IFX}.10.2": 2000,
        f"{IFX}.15.2": 2500,
        f"{IFX}.19.2": 0,
    }


def test_exact_mib_mapping_and_derived_values(raw):
    s = build_snapshot(raw, now=1)
    assert s.serial == "TEST-NAS-001"
    assert s.readings["cpu_usage"].value == 1.75
    assert s.readings["disk_1_status"].value == "Good"
    assert s.readings["disk_1_smart"].value == "Healthy"
    assert s.readings["volume_1_used"].value == 7477
    assert s.readings["volume_1_usage"].value == pytest.approx(7477 / 14776 * 100)
    assert s.readings["memory_1_used"].value == 668
    assert s.readings["memory_1_usage"].value == pytest.approx(668 / 1828 * 100)
    assert s.readings["adm_update"].value is False
    assert s.readings["disk_1_problem"].value is False
    assert s.readings["temperature_1_system"].value is None
    assert not any(k.startswith("ups_") for k in s.readings)


@pytest.mark.parametrize(
    "status, expected",
    [
        ("Available", True),
        ("Unavailable", False),
        ("The ADM is latest version", False),
        ("", None),
        ("Checking for updates", None),
        ("Unbekannt", None),
    ],
)
def test_update_status_is_not_guessed(raw, status, expected):
    raw[f"{V}.1.7.0"] = status
    assert build_snapshot(raw, now=1).readings["adm_update"].value is expected


def test_unknown_health_is_not_healthy(raw):
    raw[f"{V}.4.1.1.6.1"] = "checking"
    assert build_snapshot(raw, now=1).readings["disk_1_problem"].value is None
    raw[f"{V}.4.1.1.5.1"] = "Failed"
    assert build_snapshot(raw, now=1).readings["disk_1_problem"].value is True


def test_impossible_volume_and_memory_are_unknown(raw):
    raw[f"{V}.5.1.1.7.1"] = 20000
    raw[f"{V}.2.6.1.2.1"] = 0
    s = build_snapshot(raw, now=1)
    assert s.readings["volume_1_usage"].value is None
    assert s.readings["volume_1_used"].value is None
    assert s.readings["memory_1_usage"].value is None


def test_capacity_units_are_explicit(raw):
    s = build_snapshot(raw, now=1, options={"storage_unit": "GB", "memory_unit": "MB"})
    assert s.readings["disk_1_size"].unit == "GB"
    assert s.readings["memory_1_used"].unit == "MB"


def test_network_rates_use_elapsed_time_and_counter64(raw):
    first = build_snapshot(raw, now=100)
    key = "net_65746830_rx_rate"
    assert first.readings[key].value is None
    raw[f"{IFX}.6.2"] += 15_000_000
    raw[f"{IFX}.10.2"] += 7_500_000
    second = build_snapshot(raw, now=130, previous=first.network)
    assert second.readings[key].value == 4.0
    assert second.readings["net_65746830_tx_rate"].value == 2.0


@pytest.mark.parametrize("change", ["decrease", "reboot", "discontinuity", "reindex", "gap"])
def test_counter_discontinuities_do_not_create_spikes(raw, change):
    first = build_snapshot(raw, now=100)
    raw[f"{IFX}.6.2"] += 1000
    now = 130
    if change == "decrease":
        raw[f"{IFX}.6.2"] = 1
    elif change == "reboot":
        raw["1.3.6.1.2.1.1.3.0"] = 10
    elif change == "discontinuity":
        raw[f"{IFX}.19.2"] = 10
    elif change == "reindex":
        raw = {
            k[:-1] + "3" if k.startswith((IF + ".", IFX + ".")) else k: v for k, v in raw.items()
        }
    else:
        now = 2000
    s = build_snapshot(raw, now=now, previous=first.network)
    assert s.readings["net_65746830_rx_rate"].value is None


def test_virtual_interfaces_opt_in(raw):
    raw[f"{IF}.2.2"] = raw[f"{IFX}.1.2"] = "docker0"
    assert not any(k.startswith("net_") for k in build_snapshot(raw, now=1).readings)
    assert any(
        k.startswith("net_")
        for k in build_snapshot(raw, now=1, options={"include_virtual": True}).readings
    )


def test_ups_requires_identity_even_when_enabled(raw):
    options = {"enable_ups": True}
    assert not any(
        k.startswith("ups_") for k in build_snapshot(raw, now=1, options=options).readings
    )
    raw[f"{V}.6.1.1.3.1"] = "Test UPS"
    s = build_snapshot(raw, now=1, options=options)
    assert s.readings["ups_1_on_battery"].value is True
    assert s.readings["ups_1_charge"].value == 0


def test_raw_network_counters_do_not_claim_reset_safe_statistics(raw):
    snapshot = build_snapshot(raw, now=1)
    assert snapshot.readings["net_65746830_rx_total"].state_class is None
    assert snapshot.readings["net_65746830_tx_total"].state_class is None
