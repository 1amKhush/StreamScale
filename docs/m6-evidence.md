# M6 acceptance evidence

Started 2026-10-08T10:17:16Z; completed 2026-10-08T10:46:37Z. The full
`./streamscale.sh all --no-ui` path passed: 118 unit tests, 28 live integration
tests, Ruff checks, Helm lint, fresh fixed SQL/metrics/Grafana verification,
three scaling exercises, and a final restored fixed demo. Console evidence:
`artifacts/m6-full-check.log`. Local review completed; CodeRabbit 0.7.0 was signed
out, so its remote review was unavailable.

| Mode | Run ID | Maximum ready consumers | Unique facts | Isolation / bounds |
| --- | --- | --- | --- | --- |
| fixed | `572dc8d0-0e77-4c80-bfc4-b5c289b145b3` | 1 | 5940 | Passed, exactly one target |
| cpu-hpa | `fb3ff497-4671-40de-917e-129bccabb013` | 3 | 5940 | Passed, 1–3 target |
| lag-keda | `d637d401-c408-4e0e-9c0b-2592786a0a74` | 3 | 5940 | Passed, 1–3 target |

All modes used the same `streamscale:226a373558fc7450` build, image index
`sha256:b548a15ccd5f132da43ce2b081708b15db3e296206f2859f84023530e4afcd04`,
resources, seed 42, 3000 orders and correctness profile. Each had a fresh
directory, UUID/group, controller history and successful SQL/DLQ reconciliation.
The combined result is `artifacts/k8s/scaling-verification.json`; per-run evidence
is under its recorded `run_name` directories.

Metrics Server 0.9.0 and KEDA 2.21.0 were installed with verified upstream
manifest/chart checksums and immutable multi-architecture image digests.
Resource and external metrics APIs were available. KEDA's operator logged that
Kubernetes 1.37 was outside its upstream-tested versions; actual Kafka scale-out
and reconciliation passed on this dedicated kind cluster. This local exercise
does not establish compatibility with other cluster versions.

Defects found and corrected during live acceptance:

- Metrics Server's 10s resolution initially equalled its request timeout; use
  a 5s request timeout and the local kind kubelet TLS exception. Initial image
  pulls exceeded KEDA's five-minute install deadline; the pinned installation
  now allows ten minutes and reuses cached verified images.
- Helm 4 server-side apply conflicted with HPA ownership of replicas. Autoscaled
  manifests now omit that field; mode switches remove previous controllers,
  reset the drained Deployment, and apply with client-side Helm updates.
- KEDA's namespace could not resolve Kafka's short advertised hostname. Both
  bootstrap and advertised broker use the namespace-qualified service address.

The interrupted run `6ffb47ae-08b0-4af9-b0d4-4667194f1e65` was recovered before
starting fresh validation, preserving all 5940 facts and reaching zero lag.
No database, broker, volume or cluster reset was performed. The launcher and
run/scaling guides cover the new stage. Next: M7 pilot and measured matrix.
