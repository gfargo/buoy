# Security Policy

## Supported Versions

Buoy is an Alpha-stage project (see `pyproject.toml` classifiers). Only the
latest `2.1.x` release is supported with security fixes.

| Version | Supported          |
| ------- | ------------------ |
| 2.1.x   | :white_check_mark: |
| < 2.1   | :x:                |

## Threat Model

Buoy is designed for **private networks** (home LAN, Tailscale, VPN) and is
NOT designed to be internet-facing. See [SPEC.md §7.1](SPEC.md#71-threat-model)
for the full threat model. If you're exposing a buoy instance to the public
internet, that's outside the supported deployment model — please put it behind
a VPN or reverse proxy with auth instead.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, report vulnerabilities privately using one of:

- [GitHub private security advisories](https://github.com/gfargo/buoy/security/advisories/new)
  (preferred)
- Email: ghfargo@gmail.com

Please include as much detail as possible: affected version, reproduction
steps, and potential impact. We'll acknowledge your report as soon as we can
and keep you updated as we work on a fix.
