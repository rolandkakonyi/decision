# Security policy

## Reporting a vulnerability

Email [rolandkakonyi@gmail.com](mailto:rolandkakonyi@gmail.com) to report a
vulnerability privately. Do not disclose vulnerabilities in public issues or
pull requests before a fix is available.

Include the affected version and provider, impact, and a minimal reproduction
using synthetic data. Do not send real API keys, private images, customer data,
or complete provider responses containing sensitive context. If a credential
was exposed, revoke it with its provider.

## Supported versions

Security fixes target the latest release. Update to the latest release when
reporting a problem; older versions do not receive separate backports.

## Data and credentials

Decision sends the state, questions, and referenced images to the configured
provider. Only supply data you are authorized to send there. Provider accounts,
API charges, and data-handling terms are managed separately from this project.

Credentials are read from environment variables. Env files are not loaded
automatically. A custom provider host requires an explicit credential selector
or `DECISION_API_KEY`; standard provider keys are used only for their matching
official HTTPS host. MCP callers cannot change provider routing, account ID,
or the credential selector.

Treat request files, dry-run previews, and successful output as potentially
sensitive. MCP accepts base64 image data rather than local file paths. Run
the STDIO MCP server through a trusted local client and share only the
environment variables needed by that provider.
