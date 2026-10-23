# Dependency licenses

Project-authored source is under the root [MIT license](../LICENSE).
Dependencies retain their own licenses; the project license does not relicense
their code. The source repository does not vendor dependency implementations.

The installed metadata reviewed on 2026-10-24 identifies these direct runtime
dependency licenses:

| Package | Reviewed version | Reported license |
|---|---|---|
| Typer | 0.27.3 | MIT |
| Pydantic | 2.14.1 | MIT |
| google-api-python-client | 2.201.0 | Apache-2.0 |
| google-auth-oauthlib | 1.5.0 | Apache-2.0 |

Reviewed transitive components include google-auth/google-auth-httplib2/requests
(Apache-2.0), httplib2/pydantic-core/Rich (MIT), oauthlib (BSD-3-Clause),
requests-oauthlib (BSD), and cryptography (Apache-2.0 OR BSD-3-Clause).
Pytest, used for development, reports MIT. These are permissive licenses; no
direct runtime dependency reviewed here reports a copyleft requirement.

This is a metadata-based review of the installed versions, not an exhaustive
legal opinion or a promise about future dependency versions. Pip installs
dependencies as separate distributions with their license/notice files. Preserve
those files and any required notices when redistributing dependencies, bundled
executables or containers; review the full resolved dependency set for such a
release. No dependency files are copied into this source license.
