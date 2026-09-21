# Validation and release evidence

Release: **1.0.0**, 21 September 2026.

## Executed checks

- Home Assistant **2026.9.2**, Python **3.14.7**, PySNMP **7.1.27**.
- **53 automated tests passed**; integration statement coverage **92%**
  (599 statements; 50 not exercised).
- Ruff checks and formatting passed; all integration files compiled successfully.
- Installer tested for first installation, upgrade backup, rollback following a
  simulated replacement failure, and refusal of a non-HA configuration directory.
- Archive contents, per-file hashes, manifest version and installation layout
  were verified when producing the release ZIP.

The tests use the actual Home Assistant runtime and actual PySNMP package.
Loopback UDP tests exercise SNMPv3 MD5/authNoPriv, SHA/AES authPriv, SNMPv2c,
identity probing, full vendor/network walks and invalid credentials. An end-to-end
HA test sets up the integration through real SNMP traffic, checks a sensor state,
then stops HA and verifies transport closure stays on the event-loop thread.
A local test agent is not evidence of compatibility with every physical NAS.

Other tests cover UI setup/errors, duplicate serials, reauthentication,
reconfiguration, options/reload, unknown readings, dynamic entity discovery,
removal, outage recovery, resource cleanup, credential encoding, diagnostics,
walk boundaries/limits, optional network denial, reset-safe rates and invalid
vendor values. No live credentials or device serials are embedded in fixtures.

## Independent review and fixes

A separate reviewer inspected the complete integration. All substantive findings
were addressed and relevant regressions were reproduced before their fixes:

1. HA stop handler now awaits asynchronous coordinator shutdown on the event loop.
2. Shutdown cancels and awaits an active poll; a generation guard prevents a
   connection still opening from becoming active after closure.
3. SNMP view/access denial is separate from USM authentication failure, allowing
   vendor monitoring to continue when optional IF-MIB access is denied.
4. Raw network counters no longer declare reset-safe long-term statistics;
   throughput measurements retain explicit continuity checks.
5. Unsupported credential encoding is rejected in the UI; expected ASN.1 errors
   become sanitised connection errors.
6. Serial mismatch closes the connection and clears previous rate baselines.

No substantive review finding remains deferred.

## Limits and observed dependency warning

- The integration has not been installed into the user's running HA instance
  and has not been directly tested against the user's physical NAS.
- Hardware mappings are grounded in the supplied MIBs and pasted SNMP readings.
  Storage GiB and memory MiB defaults remain documented unit inferences.
- Direct UDP protocol tests cover MD5, SHA/AES and v2c. SHA-256/SHA-512, DES,
  noAuthNoPriv and IPv6 paths are supported through the PySNMP API but were not
  independently exercised against a physical device or a separate protocol test.
- The encrypted AES test produced one upstream `CryptographyDeprecationWarning`
  inside PySNMP's CFB implementation with cryptography 48.0.1. The test passed.
  The confirmed MD5/authNoPriv NAS mode does not use this encryption path.
  Recheck compatibility before changing the pinned dependency or moving to a
  future HA release with cryptography 49 or later.
- There is no public repository, automatic update feed, formal HA quality-scale
  certification, or long-running physical-hardware soak test for this release.

The release is installable and locally verified. Physical-device validation is
still required before making an unqualified claim of production reliability.
