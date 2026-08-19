# Security Policy

## Supported deployment boundary

既有版本 supports two explicit modes:

1. `SAT_SIM_AUTH_MODE=disabled`: trusted single-user local development only; bind to `127.0.0.1`.
2. `SAT_SIM_AUTH_MODE=token`: authenticated control-plane and remote-Worker deployment behind an approved HTTPS reverse proxy.

既有版本 provides Bearer Token authentication, viewer/operator/admin/worker RBAC, Worker-ID binding, request-size and host-header limits, security response headers, append-only API audit logs, and integrity-checked remote Run Bundle transfer. It does not provide enterprise IAM, OAuth2/OIDC, multi-tenant isolation, automatic token rotation, or a consensus-backed distributed scheduler.

## Reporting a vulnerability

Report suspected vulnerabilities privately to the project delivery owner. Include the affected version, reproduction steps, impact, and sanitized logs. Do not publish exploit details before assessment.

## Sensitive configuration

- Keep Token values and provider API keys in environment variables or a secrets manager.
- Auth JSON files should contain only environment-variable names or SHA-256 token digests.
- Restrict auth/environment files to the service account (`0600` or stricter equivalent).
- Restrict model-provider base URLs and remote Worker control-plane URLs to approved endpoints.
- Do not put credentials, customer data, restricted telemetry or private prompts in distributable Run Bundles.
- Verify release and Run Bundle SHA-256 manifests before installation or result acceptance.
- Use HTTPS for every networked deployment; `--insecure-skip-tls-verify` is only for isolated test fixtures.

## Remote Worker trust boundary

Remote Workers execute only prepared TaskSpec/ExecutionPlan bundles and return sealed Run Bundles. The control plane rejects path traversal, symlinks, protocol mismatch, run-ID mismatch, ExecutionPlan hash mismatch, unsealed output and artifact hash failure. Workers still require an OS account, container or host security boundary appropriate to the deployment.

## Remaining out of scope

- Enterprise identity federation and per-user tenancy;
- automatic Token lifecycle and central revocation;
- strong container sandboxing of untrusted tenant workloads;
- Redis/PostgreSQL/Kubernetes multi-primary scheduling;
- exactly-once physical execution under simultaneous control-plane, network and storage failures;
- automatic certificate issuance or private-key management.
