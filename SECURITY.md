# Security

This repository is an educational sample with fictional, in-memory data and no
production integrations.

Do not use the sample policy or process-local evidence registry unchanged in
production. Store signed envelopes in a durable, session-scoped capability
store, keep the signing secret in a managed secret service, and add expiry,
nonce and replay protection, deployment binding, key rotation, replica-safe
lookup, and durable audit correlation.
Require human approval for actions whose impact exceeds the sample's bounded
authority, and test fail-closed behavior in your own hosting environment.

Report a vulnerability through GitHub private vulnerability reporting for
this repository. Do not open a public issue containing secrets or customer
data.
