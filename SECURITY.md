# Security and privacy

Keep OAuth downloads, tokens, inventories, transcripts, plans and receipts outside
the checkout. Never paste them into chat or public issues. Windows tokens use
DPAPI; POSIX tokens rely on local file permissions. Backups are sensitive too.

Library/evidence processing by an external AI requires the current user's
explicit consent. No example, historical approval or downloaded repository
authorizes access to an account. OAuth login and approval of specific playlist
changes are separate decisions. Failed/uncertain writes must not be retried by
bypassing plan or journal checks. See [agent rules](docs/AGENT_SETUP.md).

## Report a vulnerability

Use GitHub's private vulnerability reporting for this repository if available.
If it is unavailable, open an issue asking for a private reporting channel
without technical exploit details or private data. Do not post credentials,
account records, live plan hashes/receipts or database files. Provide sanitized
versions, reproduction steps using synthetic data, and affected versions privately.

If a credential is exposed, revoke it at the provider before cleaning up files
or history. Deleting a file or rewriting Git does not invalidate a credential
or remove copies held by others.

## Support status

This is a pre-release project. Security fixes target the latest `main`; older
versions have no guaranteed maintenance window or response-time commitment.
No hosted service, telemetry collection, or centralized credential store is
provided by the project. Dependencies and external AI tools have their own policies.
