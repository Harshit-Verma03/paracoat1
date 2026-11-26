# Security policy

## Project status

Paracoat Dispatch Studio is an early, local, single-user prototype. It has no authentication or role-based access control and uses SQLite by default. Do not expose an instance containing operational, customer, route, or personal data to the public internet.

The app includes planning estimates and synthetic demonstration data. It is not a certified cargo-safety tool.

In road-routing mode, delivery coordinates are sent to the configured routing endpoint. The default public OSRM demo service is for development; use straight-line estimates or a trusted provider for sensitive locations.

## Reporting a vulnerability

Please do not publish credentials, private data, or an unpatched vulnerability in a public issue. If GitHub private vulnerability reporting is enabled for the repository, use it to report the issue privately. Otherwise, contact the repository owner through their GitHub profile and request a private reporting channel.

Include affected versions/commits, steps to reproduce, and potential impact. Do not include real customer or shipment data.
