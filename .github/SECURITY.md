# Security Policy

This is a fork (Ariadne) of [Penelope](https://github.com/brightio/penelope)
by brightio. A vulnerability in logic inherited unchanged from upstream likely
affects Penelope too and is worth reporting there as well.

## Supported Versions

Only the latest release of Ariadne is actively supported with security updates.

| Version | Supported |
|---|---|
| Latest release | Yes |
| Previous releases | No |

## Reporting a Vulnerability

Please **do not open a public GitHub issue** for suspected security vulnerabilities.

The preferred way to report a vulnerability is through GitHub's private vulnerability reporting feature on this fork's repository:

**Security → Advisories → Report a vulnerability**

Please include as much relevant information as possible, such as:

- A description of the vulnerability
- Affected version(s)
- Steps to reproduce
- Proof of concept, if available
- Potential security impact
- Suggested fix, if available

We aim to acknowledge security reports within **72 hours**.

If the issue is confirmed, we will coordinate remediation and disclosure with the reporter.

Confirmed vulnerabilities may be published as a GitHub Security Advisory and assigned or requested a CVE when appropriate.

## Security Considerations

Ariadne interacts with potentially hostile systems by design.

Some behaviors that may appear security-sensitive are inherent to its operation or are documented risks rather than vulnerabilities.

For known security characteristics, trust assumptions, and operational risks, please review the [Security considerations](../README.md#security-considerations) section of the README before submitting a report.

## Disclosure

Please allow reasonable time for a vulnerability to be investigated and fixed before publicly disclosing technical details.

We appreciate responsible disclosure and will credit reporters in the corresponding security advisory unless they prefer to remain anonymous.
