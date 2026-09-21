# ASUSTOR SNMP implementation plan

Goal: deliver the integration defined in DESIGN.md.
Architecture: pure telemetry normalization -> asynchronous SNMP client -> HA
coordinator -> dynamic entities and UI flows. Implement inline, then obtain one
independent whole-project code review.

1. Telemetry: add sample-based failing tests for exact MIB mappings, unit
   conversions, invalid readings, status ambiguity and reset-safe network rates.
   Implement const.py and telemetry.py. Run pytest tests/test_telemetry.py.
2. Transport: add failing tests for bounded walks, unsupported OIDs, auth errors,
   timeouts, subtree escape, and optional-network isolation. Implement snmp.py
   and coordinator.py. Run the full suite and local UDP protocol validation.
3. HA lifecycle: add failing tests for setup, duplicate identity, reauth, options,
   failed connection, unload, missing readings and dynamic entity creation.
   Implement config_flow.py, __init__.py, entity.py, sensor.py,
   binary_sensor.py, diagnostics.py and translations. Run the full suite.
4. Review failure modes: index changes; hot removal; unknown localized status;
   network failure between samples; partial walks and resource cleanup.
   Fix substantive findings with regression tests and repeat full validation.
5. Package: compile/lint, verify archive contents and installation layout,
   write README and test evidence, build versioned ZIP and save deliverables.

Implementation evidence and decisions are recorded in VALIDATION.md.
