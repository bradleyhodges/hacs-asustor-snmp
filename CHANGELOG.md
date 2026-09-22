# Changelog

## 1.1.0

- Improve integration documentation
- Move non-default SNMPv3 options to Advanced section of integration initial setup flow (SNMP version, port, security level, auth protocol, privacy protocol, SNMPv2 community, and context name)
- Add integration branding
- Remove unused docs directory

## 1.0.0

- Initial read-only ASUSTOR SNMP integration with UI setup, reconfiguration,
  reauthentication and monitoring options.
- SNMPv3 MD5/SHA/SHA-256/SHA-512 authentication; optional AES/DES privacy; v2c.
- Dynamic system, disk, volume, interface and optional UPS entities.
- Invalid-value filtering, conservative health parsing, reset-safe network rates,
  bounded polling, optional-network failure isolation and allowlisted diagnostics.
- Real Home Assistant and localhost SNMP protocol regression tests.
- Backup-aware manual installer, installation guide and future HACS metadata.
