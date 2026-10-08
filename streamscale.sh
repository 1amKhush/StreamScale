#!/usr/bin/env bash
# The single project entry point. Extend all() whenever another milestone is integrated.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

CLUSTER="streamscale"
LOCAL_PROJECT="streamscale-local"
KUBECTL="$ROOT/.tools/bin/kubectl"
KUBECONFIG_FILE="$ROOT/artifacts/kubeconfig"

usage() {
    cat <<'HELP'
StreamScale launcher — integrated milestones M0–M6

Usage: ./streamscale.sh [command] [--no-ui]

  all [--no-ui]  Setup, local checks, cluster demo, three scaling modes, dashboards.
                This is the default. --no-ui finishes without opening port forwards.
  local         Setup and run a fresh local Kafka/PostgreSQL fixture demo.
  check         Start local dependencies and run unit, lint and live integration checks.
  cluster       Setup and deploy/verify a fresh kind cluster execution.
  scaling       Exercise fixed/CPU-HPA/lag-KEDA independently; retain replica evidence.
  verify        Verify the current fresh cluster execution without republishing.
  inspect-local Reinspect the latest local demo made by this launcher.
  ui            Open Grafana/Prometheus forwards; Ctrl+C closes the forwards.
  status        Show this project's containers and, when running, Kubernetes resources.
  stop          Stop local services and kind node containers; keep all stored data.
  stop-local    Stop only the local Compose services; keep their data.
  stop-cluster  Stop only the kind node containers (all cluster pods); keep cluster data.
  resume        Start retained local services and kind nodes; do not create a new run.
  help          Show this help.

Prerequisites: Bash, uv, make, and a running Docker Engine with Compose.
Grafana: http://127.0.0.1:13000/d/streamscale
Prometheus: http://127.0.0.1:19090
Grafana login: artifacts/k8s/credentials.json

Close the dashboard terminal with Ctrl+C before running stop in another terminal.
all/local/cluster create fresh executions and preserve earlier runs.
After resume, use all or cluster for a fresh counter-based acceptance check.
Cluster verification waits up to 60 seconds for metrics to reflect SQL reconciliation.
HELP
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        printf 'Missing command: %s. See docs/run-project.md.\n' "$1" >&2
        exit 1
    fi
}

docker_ready() {
    require_command docker
    if ! docker info >/dev/null 2>&1; then
        printf 'Start Docker Engine/Desktop and make sure your user can access Docker.\n' >&2
        exit 1
    fi
}

setup() {
    require_command uv
    require_command make
    docker_ready
    docker compose version >/dev/null
    if [[ ! -f .env ]]; then
        (umask 077; cp .env.example .env)
        printf 'Created .env from the local example.\n'
    fi
    uv sync --frozen
}

containers() {
    docker ps -a --filter "label=$1" --format '{{.Names}}'
}

container_action() {
    local operation="$1" selector="$2" listing name
    local names=()
    listing="$(containers "$selector")"
    while IFS= read -r name; do
        if [[ -n "$name" ]]; then
            names+=("$name")
        fi
    done <<< "$listing"
    if [[ ${#names[@]} -gt 0 ]]; then
        if [[ "$operation" == stop ]]; then
            docker stop --timeout 45 "${names[@]}"
        else
            docker start "${names[@]}"
        fi
    else
        printf 'No containers found for %s.\n' "$selector"
    fi
}

start_cluster_nodes() {
    container_action start "io.x-k8s.kind.cluster=$CLUSTER"
    if [[ -x "$KUBECTL" && -f "$KUBECONFIG_FILE" &&
          -n "$(containers "io.x-k8s.kind.cluster=$CLUSTER")" ]]; then
        wait_for_api
    fi
}

local_start() {
    make up
    make bootstrap
}

local_demo() {
    local base directory
    mkdir -p artifacts/launcher
    base="$(mktemp -d "$ROOT/artifacts/launcher/demo-XXXXXXXX")"
    directory="$base/local-run"
    make demo "RUN_DIR=$directory"
    printf '%s\n' "$directory" > artifacts/launcher/last-local-run.path
    printf '\nLocal run artifacts: %s\n' "$directory"
}

checks() {
    make test
    make check
    make integration
}

cluster_demo() {
    # kind lists stopped nodes too; start retained nodes before Helm talks to the API.
    start_cluster_nodes
    make k8s-up
    make k8s-verify
}

kube() {
    "$KUBECTL" --kubeconfig "$KUBECONFIG_FILE" --context "kind-$CLUSTER" \
        --namespace streamscale "$@"
}

wait_for_api() {
    local deadline=$((SECONDS + 180))
    printf 'Waiting for the retained cluster API...\n'
    until kube --request-timeout=5s get --raw=/readyz >/dev/null 2>&1; do
        if [[ $SECONDS -ge $deadline ]]; then
            printf 'Cluster API did not become ready. Inspect with ./streamscale.sh status.\n' >&2
            return 1
        fi
        sleep 2
    done
}

status() {
    docker_ready
    printf '\nLocal services:\n'
    docker ps -a --filter "label=com.docker.compose.project=$LOCAL_PROJECT" \
        --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    printf '\nCluster nodes:\n'
    docker ps -a --filter "label=io.x-k8s.kind.cluster=$CLUSTER" \
        --format 'table {{.Names}}\t{{.Status}}'
    local running
    running="$(docker ps --filter "label=io.x-k8s.kind.cluster=$CLUSTER" --format '{{.Names}}')"
    if [[ -n "$running" && -x "$KUBECTL" && -f "$KUBECONFIG_FILE" ]]; then
        kube get pods,jobs,svc,pvc
    else
        printf 'Cluster is absent or stopped. Use cluster for a new run, or resume for retained data.\n'
    fi
}

ui() {
    require_command uv
    # Replace the launcher so Ctrl+C goes to the foreground dashboard command.
    exec uv run --frozen python deploy/k8s/access.py
}

resume() {
    setup
    local_start
    local nodes
    nodes="$(containers "io.x-k8s.kind.cluster=$CLUSTER")"
    if [[ -n "$nodes" ]]; then
        start_cluster_nodes
        if [[ ! -x "$KUBECTL" || ! -f "$KUBECONFIG_FILE" ]]; then
            printf 'Cluster tools/config are missing. Run ./streamscale.sh cluster.\n' >&2
            exit 1
        fi
        kube wait --for=condition=Ready nodes --all --timeout=180s
        kube rollout status statefulset/streamscale-kafka --timeout=180s
        kube rollout status statefulset/streamscale-postgres --timeout=180s
        kube rollout status deployment/streamscale-consumer --timeout=180s
        kube rollout status deployment/streamscale-exporter --timeout=180s
        kube rollout status deployment/streamscale-prometheus --timeout=180s
        kube rollout status deployment/streamscale-grafana --timeout=180s
    fi
    printf 'Retained services resumed. No new workload was published.\n'
    status
}

command="${1:-all}"
if [[ $# -gt 0 ]]; then shift; fi
no_ui=false
if [[ "$command" == all && "${1:-}" == --no-ui ]]; then
    no_ui=true
    shift
fi
if [[ $# -gt 0 ]]; then
    printf 'Unexpected argument: %s\n' "$1" >&2
    usage >&2
    exit 2
fi

case "$command" in
    all)
        setup
        printf '\n[M0–M4] Starting the local pipeline, smoke test and fresh fixture demo.\n'
        local_start
        make smoke
        local_demo
        checks
        printf '\n[M5] Deploying a fresh cluster execution and checking SQL/metrics/Grafana.\n'
        cluster_demo
        printf '\n[M6] Exercising the three isolated scaling modes and replica bounds.\n'
        make scaling
        status
        printf '\nM0–M6 passed. Run artifacts are under artifacts/.\n'
        if [[ "$no_ui" == false ]]; then ui; fi
        ;;
    local) setup; local_start; make smoke; local_demo ;;
    check) setup; local_start; checks ;;
    cluster) setup; cluster_demo ;;
    scaling) setup; start_cluster_nodes; make scaling ;;
    verify) require_command make; make k8s-verify ;;
    inspect-local)
        require_command uv
        if [[ ! -f artifacts/launcher/last-local-run.path ]]; then
            printf 'No launcher demo found. Run ./streamscale.sh local first.\n' >&2
            exit 1
        fi
        IFS= read -r directory < artifacts/launcher/last-local-run.path
        uv run --frozen --env-file .env streamscale inspect --run-dir "$directory"
        ;;
    ui) ui ;;
    status) status ;;
    stop|stop-local|stop-cluster)
        docker_ready
        if [[ "$command" != stop-cluster ]]; then
            container_action stop "com.docker.compose.project=$LOCAL_PROJECT"
        fi
        if [[ "$command" != stop-local ]]; then
            container_action stop "io.x-k8s.kind.cluster=$CLUSTER"
        fi
        printf 'Stopped selected StreamScale services. Data is retained; use resume to restart.\n'
        ;;
    resume) resume ;;
    help|--help|-h) usage ;;
    *) printf 'Unknown command: %s\n' "$command" >&2; usage >&2; exit 2 ;;
esac
