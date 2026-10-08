# Monitoring definitions

The Helm chart packages the single Prometheus/Grafana stack. Its authoritative
definitions are in `deploy/k8s/chart/files/`: `prometheus.yaml`,
`grafana-datasource.yaml`, `grafana-provider.yaml` and `dashboard.json`.
Deploying through `make k8s-up` uses these files directly, with configuration
checksums triggering monitoring rollout when they change.

Metric definitions, access commands and raw acceptance exports are documented
in [deployment-and-metrics.md](../docs/deployment-and-metrics.md).
