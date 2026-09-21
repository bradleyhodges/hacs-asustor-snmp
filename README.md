# ASUSTOR NAS (SNMP) for Home Assistant

Version **1.0.0**. A local, read-only custom integration with UI configuration,
SNMPv3 authentication, automatic entity discovery and native Home Assistant
sensors. Developed against **Home Assistant 2026.9.2**, Python 3.14 and
**PySNMP 7.1.27**, matching HA's built-in SNMP dependency in that release.

The integration was tested in that actual HA version and against a local SNMP
agent. It has **not yet been installed or tested against your physical NAS**.
See `docs/VALIDATION.md` for test evidence and remaining limitations.

## Install in your Home Assistant Container setup

Download `ASUSTOR-SNMP-1.0.0.zip` to the **machine running Home Assistant**.
Extract it:

```bash
unzip ASUSTOR-SNMP-1.0.0.zip -d asustor-snmp-1.0.0
cd asustor-snmp-1.0.0
```

Check the container name and locate its configuration directory:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Image}}'
HA_CONTAINER=homeassistant
HA_CONFIG_DIR="$(sudo docker inspect "$HA_CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}')"
printf 'Home Assistant configuration: %s\n' "$HA_CONFIG_DIR"
```

Change `HA_CONTAINER` if your container has a different name. Check that the
printed directory is your existing HA configuration directory. Then install:

```bash
sudo python3 install.py --config-dir "$HA_CONFIG_DIR"
sudo docker restart "$HA_CONTAINER"
```

The installer needs Python 3.10+ on the host. It verifies the staged files,
preserves any previous version outside `custom_components`, and restores the
previous directory if replacement fails. It does not edit `configuration.yaml`
or restart HA itself. The final command above explicitly restarts the container.
Do not run the installer inside the container with the host path.

Alternatively, copy **only** the supplied `custom_components/asustor_snmp`
directory to your HA configuration's `custom_components` directory, then restart
HA. The resulting path inside the container must be:

```text
/config/custom_components/asustor_snmp/manifest.json
```

Home Assistant installs the declared PySNMP dependency automatically; it needs
outbound access to its configured Python package index during first setup.
There is no need to install Net-SNMP commands or upload the vendor MIB files.

After restarting:

1. Open **Settings → Devices & services → Add integration**.
2. Search for **ASUSTOR NAS (SNMP)**.
3. Enter the following settings, confirmed by your successful SNMP walk:

| Setting | Value |
|---|---|
| Device name | `Plexstore`, or your preferred name |
| Host | `192.168.33.23` |
| Port | `161` |
| SNMP version | `3` |
| Username | `homeassistant` |
| Security level | `authNoPriv` |
| Authentication protocol | `MD5` |
| Authentication passphrase | Your current NAS SNMP password |
| Privacy passphrase | Leave blank |
| Community | Leave blank |
| Context name | Leave blank |

The privacy protocol selection has no effect under `authNoPriv`. This mode
provides authentication but does not encrypt telemetry. Restrict UDP/161 access
to trusted monitoring hosts. Credentials are stored in HA's config entry, like
other local integrations; protect HA's configuration directory and backups.
No credentials from your conversation are embedded in this package.

The hostname/IP must be reachable **from the HA container**. A successful walk
on a different machine does not itself prove that routing/firewall path works.

## What appears in Home Assistant

One NAS device groups the discovered entities. Names and counts vary with the
NAS and its supported MIB rows.

| Group | Entities |
|---|---|
| System | Overall CPU usage, per-core usage, CPU/system temperatures, fan RPM, memory total/free/used/usage, vendor-reported free percentage, uptime, ADM/BIOS version, timezone, ADM update status and availability |
| Each disk bay | Disk status, SMART status, problem indication, temperature, capacity; model and interface attributes |
| Each volume | Health status, problem indication, total/free/used capacity, usage percentage; RAID level and filesystem attributes |
| Each interface | Link status, link speed, RX/TX rate, raw received/sent byte counters, errors and discards where present |
| UPS, when enabled | Status, on-battery and low-battery states, battery charge, remaining runtime and input voltage |

Per-core values, static capacities, raw counters, link speeds and other noisy
or secondary diagnostics are disabled by default. Enable them from the device's
entity list. Invalid/unsupported readings are unknown; removed table rows are
unavailable. New table rows are discovered automatically on a successful poll.
Entities representing removed hardware remain in the registry to preserve user
customisations; remove unwanted entities manually.

Disk identity follows the **bay/index**, because this MIB supplies no disk serial.
Replacing a disk in the same bay retains that bay's entities and history. Network
identity follows `ifName`; rate baselines additionally check interface index and
MAC address. Renaming an interface creates a new set of entities.

## Monitoring options and semantics

Open the integration's **Configure** action to adjust:

- Polling interval: 30 seconds by default; 15–3,600 seconds supported.
- Per-request timeout: 3 seconds by default; 1–10 seconds supported.
- Request retries: one by default; zero to three supported.
- Virtual interfaces: excluded by default, including loopback and Docker bridges.
- UPS monitoring: off by default. Enable it only when you have a UPS. Rows with
  neither a manufacturer nor a model are ignored even when enabled. Your sample
  contained an unidentified all-zero row with `OB`, so exposing it as a real UPS
  would have created a misleading alarm.
- Storage and memory units, as explained below.

SNMP tables are limited to 4,096 values and 512 requests per walk. Vendor polling
has a 25-second deadline; optional network polling has 15 seconds, within a
45-second overall deadline. These bounds take precedence over individual retry
settings. Polls are serialised. Large/slow NAS installations may need a longer
polling interval; raising the per-request timeout does not remove the deadlines.

**Storage units:** the vendor MIB says GB, but the observed `7452` reading for an
8 TB drive is consistent with GiB. The integration therefore defaults to **GiB**.
This is an explicit inference from your hardware/readings. Select GB if your
firmware reports decimal gigabytes. Values retain the NAS's integer precision;
this is not byte-exact capacity accounting.

**Memory units:** the vendor MIB omits a unit. The default **MiB** is inferred from
the observed `1828` total; MB is available as an option. Used memory is
`total − vendor free`; usage is derived from those two readings. Vendor free
memory is not claimed to be Linux `MemAvailable`, and it may differ from another
monitor's treatment of filesystem cache. The vendor's rounded free percentage
is a separate disabled diagnostic.

**Network rates:** RX means traffic received by the NAS; TX means traffic sent by
it. Rates use the difference between 64-bit octet counters divided by actual
monotonic elapsed time, expressed in Mbit/s. The first poll is unknown. Baselines
reset after failures, counter decreases, SNMP-agent restarts, discontinuities,
interface identity changes or excessive gaps. If the uptime/counters required to
establish continuity are absent, rates remain unknown. 32-bit counters are not
used as a throughput fallback on fast links. Raw total byte counters are optional
diagnostics without a statistics state class: interface replacement must not
create fictitious lifetime traffic in HA's long-term statistics.

**Health and updates:** fault sensors turn on only for explicitly recognised
failure states, and off only for recognised healthy states. Unrecognised or
localised strings remain unknown; the raw status sensor remains available. ADM
update availability is informational only: no firmware-install action is exposed.
Uptime is the vendor's human-readable string, not a guessed boot timestamp.

## Connection changes and troubleshooting

Use the integration menu's **Reconfigure** action to change the host or SNMP
settings. Leave a secret blank to retain it; irrelevant secrets are removed when
changing security modes. The NAS serial must remain the same. An explicit USM
credential rejection starts HA reauthentication; a silent rejection may appear
as a connection timeout because some SNMP agents do not return an error report.

- Integration missing after restart: confirm the exact `manifest.json` path;
  refresh the browser; check HA logs for `custom_components.asustor_snmp`.
- Cannot connect: check UDP/161 routing from the HA container and the NAS SNMP
  service. Use the authentication mode above; your NAS rejected SHA in the
  earlier tests. A non-default context can also restrict the visible OIDs.
- Unexpected host/device: identity requires ASUSTOR serial and model OIDs to be
  readable; setting up a different NAS requires a separate integration entry.
- Network entities unavailable while disk/system sensors work: check access to
  IF-MIB and IF-MIB's extended counters. Optional network failure or view denial
  does not discard otherwise valid vendor telemetry.
- Unknown system temperature: your sample's `-2147483648` is rejected as invalid.
- Credentials: this PySNMP path accepts Latin-1 characters; usernames and context
  names are limited to 32 bytes. Passphrases need at least eight characters.
- Diagnostics: download diagnostics from the integration menu. The output is
  allowlisted and omits credentials, host, serial, MACs, names and raw telemetry.

## Upgrade, rollback and removal

Run the new release's installer against the same configuration directory and
restart HA. The installer prints the backup path. Preserve the configured entry
and its entities when upgrading so unique IDs and history are retained.

For rollback, stop Home Assistant, move the current
`custom_components/asustor_snmp` directory aside, copy the saved backup's
`asustor_snmp` directory into `custom_components`, and start Home Assistant.
This release uses config-entry schema version 1 and does not migrate existing
third-party ASUSTOR integrations.

For removal, delete this integration entry in HA, then remove only its
`custom_components/asustor_snmp` directory and restart HA. Existing entity
history may remain according to HA's recorder retention settings.

## Development and sources

Python 3.14 is required for the test environment:

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest -q --cov=custom_components.asustor_snmp
.venv/bin/ruff check .
```

The ZIP contains full source, tests and the installer. No private network or
physical NAS access is required for the tests; protocol tests bind localhost UDP.

Mappings were derived from the three supplied ASUSTOR MIBs. Vendor MIB files are
not required at runtime and are not redistributed in this package.

- [Home Assistant coordinator documentation](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [Home Assistant config-flow documentation](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/)
- [HA 2026.9.2 SNMP dependency](https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/components/snmp/manifest.json)
- [PySNMP 7.1.27 asyncio implementation](https://github.com/lextudio/pysnmp/blob/v7.1.27/pysnmp/hlapi/v3arch/asyncio/cmdgen.py)
- [IF-MIB counter semantics, RFC 2863](https://www.rfc-editor.org/rfc/rfc2863.html)
- [ASUSTOR SNMP guidance](https://www.asustor.com/en/online/College_topic?topic=271)
