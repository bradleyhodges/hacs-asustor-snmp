# ASUSTOR SNMP integration design

Deliver an installable, read-only Home Assistant custom integration for Brad's
ASUSTOR NAS. The confirmed device uses SNMPv3 MD5 authentication without privacy.
Target and test against Home Assistant 2026.9.2 and its PySNMP 7.1.27 dependency.

Use a native asynchronous SNMP client, one coordinator per NAS, numeric OIDs,
UI setup/reauthentication/reconfiguration, serial-based identity and dynamically
discovered sensor/binary-sensor entities on one NAS device. No NAS modifications,
shell commands, MIB compilation, cloud service, or external daemon are required.

The supplied MIBs define system, CPU, fan, temperature, memory, disk, volume and
UPS telemetry. IF-MIB supplies interface status, speed and 64-bit byte counters.
Do not walk HOST-RESOURCES process tables. Use bounded walks and deadlines;
an optional network failure must not hide otherwise valid NAS telemetry.

Reject invalid temperatures, percentages and sizes. Unknown status strings must
remain unknown, never imply health. Throughput uses monotonic sample time and
resets on counter decreases, agent reboot, discontinuity or interface reindex.
Only Counter64 is used: 32-bit counters wrap too quickly on 2.5 GbE.

Memory units are absent from the MIB. Default to MiB based on the supplied 1828
reading; disk/volume values fit GiB despite the MIB saying GB. Expose explicit
MiB/MB and GiB/GB options and document these assumptions. Do not label vendor
free memory as Linux MemAvailable. Disk identity is bay-based: no disk serial
is supplied. UPS is opt-in and requires a manufacturer or model, suppressing
the unidentified all-zero row in the sample.

Bounded request retries, total poll timeout, no concurrent polls, cleanup on
failed setup/unload/shutdown, redacted diagnostics, no credentials in logs or
fixtures. Invalid credentials trigger reauthentication; ordinary outages retry
through the coordinator. UI errors contain no raw server response or secrets.

Package source, tests, changelog, licence, verified install/upgrade/rollback
instructions and a portable installation ZIP. Manual custom_components install
is supported immediately; HACS metadata is included for a future Git repository.
No repository publication or remote HA installation is required by this request.

Validation: actual HA 2026.9.2 runtime tests plus telemetry regression tests and
local SNMP protocol tests where possible. Explicitly distinguish these from a
live NAS installation, which has not been performed here.
