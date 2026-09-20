# Security Policy

## Supported Scope

ecOS and CogPOSIX are experimental. The current main branch is the active
development target; there are no production-supported releases or support SLA.
Do not deploy this prototype as a security boundary for sensitive workloads.
See the [security design and limitations](docs/security.md).

## Report a Vulnerability

Use [GitHub private vulnerability reporting](https://github.com/logforce/ecosystem/security/advisories/new).
Do not disclose unpatched vulnerabilities, credentials, personal data or private
datasets through public Issues, Discussions or Slack.

Include the affected commit, environment, impact and minimal reproduction using
synthetic data. Maintainers will assess the report and coordinate remediation and
disclosure. No response deadline or bounty is promised.

## Contribution and Automation Boundaries

Community membership does not grant write access. Changes enter through pull
requests. The sole administrator currently has a pull-request-only review bypass;
this exception must be reconsidered before additional administrators are added.
Force pushes and deletion of main are blocked by a separate ruleset.

Untrusted pull-request jobs must not receive deployment credentials. CI uses
read-only repository permissions and does not persist checkout credentials.
Do not run untrusted changes on a personal or privileged self-hosted runner.
