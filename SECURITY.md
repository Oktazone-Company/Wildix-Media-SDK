# Security Policy

## Current Scope

Version `0.1.x` is intended for source-restricted development and interoperability
testing. It does not provide SIP authentication, TLS, SRTP, tenant isolation, or abuse
controls and must not be treated as an Internet-hardened PBX.

## Deployment Requirements

- Restrict SIP and RTP ingress to trusted PBX or tunnel addresses.
- Never commit credentials, tokens, customer audio, or unredacted call logs.
- Encrypt stored recordings and define a retention policy when real callers are used.
- Put a hardened SIP proxy or session border controller in front of public deployments.
- Monitor listener exposure and dependency advisories.

## Reporting

Report vulnerabilities privately to the OktaZone engineering owner. Include affected
versions, reproduction steps, impact, and any suggested mitigation. Do not open a public
issue containing exploit details or customer data.
