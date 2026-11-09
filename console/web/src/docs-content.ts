export type DocPage = "overview" | "sale" | "scaling" | "faults" | "sessions";

export type DocSection = {
  title: string;
  paragraphs?: string[];
  steps?: string[];
  bullets?: string[];
  command?: { label: string; text: string };
  table?: { headings: string[]; rows: string[][] };
  note?: string;
  action?: { label: string; page: DocPage };
};

export type DocArticle = {
  id: string;
  label: string;
  title: string;
  summary: string;
  keywords: string[];
  sections: DocSection[];
};

export const DOC_ARTICLES: DocArticle[] = [
  {
    id: "start",
    label: "Start here",
    title: "Your first sale, in five steps",
    summary: "Create traffic, watch the queue, add capacity and check the final records.",
    keywords: ["quickstart", "beginner", "first run", "setup", "demo"],
    sections: [
      {
        title: "Open the console",
        paragraphs: [
          "Run commands from the repository root. Start Docker Engine or Desktop first. You need Bash, uv, make, Node.js 20.19 or newer and npm. Chrome is used by browser acceptance checks.",
          "For an existing deployment, run ./streamscale.sh console and open the exact login URL printed in the terminal. The URL contains your local login token; the port may differ if the default is busy.",
        ],
        command: { label: "First setup and console", text: "./streamscale.sh cluster\n./streamscale.sh console" },
        note: "For a quick pilot, use cluster and console. The all command includes full historical and predictive campaigns and can take several hours on its first execution.",
      },
      {
        title: "1. Start with one consumer",
        paragraphs: ["Open Scaling and choose Fixed. This keeps one consumer running so you can see demand outgrow capacity."],
        action: { label: "Choose a scaling mode", page: "scaling" },
      },
      {
        title: "2. Start a flash sale",
        paragraphs: ["Open Sale, choose Extended flash sale and start it. Keep the default settings for your first run. Every start creates a fresh run ID and consumer group; earlier sessions remain available."],
        action: { label: "Plan a sale", page: "sale" },
      },
      {
        title: "3. Watch demand and the queue",
        paragraphs: ["Overview shows arrivals, processing, backlog, latency and consumer readiness. When arrivals exceed processing, the queue grows. A growing backlog represents waiting work; it does not mean the system crashed."],
        action: { label: "Watch the live pipeline", page: "overview" },
      },
      {
        title: "4. Add capacity",
        paragraphs: ["Return to Scaling, select Manual and choose twelve consumers. Wait for pods to become ready and for Kafka to assign their partitions. There are twelve partitions, so useful parallelism is capped at twelve consumers."],
        action: { label: "Add consumers", page: "scaling" },
      },
      {
        title: "5. Drain and inspect the result",
        paragraphs: ["Use Stop & drain on Sale. Production ends, acknowledged work drains, then reconciliation checks Kafka, PostgreSQL and the dead-letter queue. Open Sessions for the exact SQL p95, correctness result, replay and exports."],
        note: "Live p95 is a histogram estimate. The exact end-to-end p95 is calculated from SQL timestamps after reconciliation.",
        action: { label: "Review sessions", page: "sessions" },
      },
    ],
  },
  {
    id: "workflows",
    label: "Using the console",
    title: "Know where to go and what to watch",
    summary: "A practical guide to every page, control and recorded result.",
    keywords: ["ui", "overview", "sale", "pause", "abort", "failure", "fault", "sessions", "replay", "export", "comparison", "lens", "keyboard"],
    sections: [
      {
        title: "Your workspace",
        table: {
          headings: ["Page", "Use it to"],
          rows: [
            ["Overview", "Follow live traffic through the pipeline; inspect components and find an order or event."],
            ["Sale", "Choose a workload, customize its schedule and control production."],
            ["Scaling", "Choose a strategy, change capacity or take manual control."],
            ["Failure lab", "Inject a bounded fault, observe its outcome and recover the pipeline."],
            ["Sessions", "Replay/export a session, compare strategies and read the retained measured matrix."],
            ["Docs", "Learn the system, find commands and follow guided demonstrations."],
          ],
        },
      },
      {
        title: "Plan and control traffic",
        paragraphs: ["Start with a preset. Customize the sale only when you need a different schedule, seed or malformed, duplicate and late-event percentages. Saved presets and repeating a sale reuse its logical settings, with a fresh run each time."],
        table: {
          headings: ["Control", "What it does"],
          rows: [
            ["Pause / resume", "Pause new production while consumers keep draining. Resume continues the schedule clock."],
            ["Set a rate", "Temporarily replace scheduled traffic with your rate. Follow the schedule returns to the current schedule position."],
            ["Stop & drain", "Finish production, flush sent records, drain the queue and reconcile the run."],
            ["Abort", "Discard unsent work. Already sent work still drains and the partial run is retained and reconciled."],
          ],
        },
        action: { label: "Open Sale", page: "sale" },
      },
      {
        title: "Read the pipeline",
        paragraphs: ["Select a component on the map or in the list to open its inspector. Find a record accepts an order or event ID and connects its broker acknowledgement, SQL history, current order state and dead-letter reason. Escape closes the inspector.", "Arrival and processing rates explain whether the queue will grow. Desired consumers, ready pods and consumer-group members are different counts during startup and Kafka rebalancing. Missing or failed measurements appear as unknown rather than zero."],
        note: "Moving dots illustrate sampled throughput; one dot is not one event. Turn motion off without losing any measurements.",
        action: { label: "Explore the pipeline", page: "overview" },
      },
      {
        title: "Experiment with failures",
        paragraphs: ["Choose a fault in Failure lab and read its scope, parameters and duration before injecting it. Consumer crashes demonstrate replay; CPU restrictions and slow writes demonstrate bottlenecks; Kafka and database outages demonstrate retries and recovery.", "The cluster agent owns recovery timers even when the browser closes. Recover pipeline ends active faults early and restores Kafka and PostgreSQL to one replica. Infrastructure outages affect all users of these shared services."],
        action: { label: "Open Failure lab", page: "faults" },
      },
      {
        title: "Replay, export and compare",
        paragraphs: ["Sessions records configuration, operations, telemetry and reconciliation. Replay is explicitly historical, has no live pipeline controls and supports a scrubber plus 1×, 4× and 10× playback. Export configuration.json, points.csv, journal.jsonl and reconciliation.json; charts also offer PNG downloads.", "Comparisons run the same frozen workload sequentially under each strategy as fresh runs. Only eligible, reconciled sessions are ranked. Faults, aborted runs, pacing changes and user changes to scaling policy exclude a session. One run per strategy on a shared node is a descriptive comparison, not a statistical result.", "Measured matrix shows the retained, read-only M7 campaign. Those historical results are separate from interactive runs and the held-out predictive evaluation."],
        action: { label: "Open Sessions", page: "sessions" },
      },
      {
        title: "Choose how the system is explained",
        paragraphs: ["View options changes the vocabulary: Store uses a supermarket analogy; System uses Kafka, Kubernetes and SQL names. Both show the same real measurements. Motion and presentation options are there too.", "Keyboard shortcuts: L changes the lens, M toggles motion and P enters presentation. Escape leaves presentation or closes the inspector. Shortcuts are ignored while typing in a field."],
      },
    ],
  },
  {
    id: "scaling",
    label: "Scaling & ML",
    title: "Choose capacity, or let the system choose",
    summary: "How the five strategies work, with one clear ML Predictive mode.",
    keywords: ["predictive", "machine learning", "forecast", "bayesian", "optimizer", "HGBT", "HPA", "KEDA", "replica", "override", "model", "training", "latency"],
    sections: [
      {
        title: "Pick the question you want to answer",
        table: {
          headings: ["Strategy", "Who chooses capacity?", "Useful for"],
          rows: [
            ["Fixed", "One consumer, always", "Seeing the baseline queue under a surge."],
            ["Manual", "You choose 0–12 consumers", "Rescuing a backlog or testing capacity limits."],
            ["CPU-HPA", "Kubernetes reacts to CPU relative to the pod request", "Observing a CPU-based autoscaler."],
            ["Lag-KEDA", "KEDA reacts to consumer-group lag", "Observing queue-based autoscaling."],
            ["ML Predictive", "The agent forecasts arrivals and evaluates 1–12 consumers", "Anticipating patterns while balancing latency and resource use."],
          ],
        },
        note: "Zero manual replicas pauses processing; it is not a crash. Kafka keeps the work until consumers return, subject to broker retention.",
        action: { label: "Choose a strategy", page: "scaling" },
      },
      {
        title: "Why twelve consumers?",
        paragraphs: ["Three replicas offered only two upward scaling steps. Twelve gives more room to observe gradual scaling, manual rescue, stabilization and predictive resource decisions while keeping one configuration for all modes.", "Kafka gives each partition to one consumer in the group at a time. Twelve partitions let up to twelve consumers do useful work. More consumers do not guarantee proportional throughput: the shared database and local node can become the limit. Historical three-replica measurements remain historical; use the new campaign for comparisons."],
      },
      {
        title: "Try ML Predictive",
        steps: [
          "Prepare the model and policy with predictive-prepare from a terminal. Close the console terminal with Ctrl+C first; campaigns refuse to compete with an active console server or sale.",
          "Reopen the console. In Scaling choose ML Predictive and keep the default p95 latency objective of two seconds.",
          "In Sale choose ML Predictive pilot, then start it. In Overview follow arrivals, the forecast, backlog and ready consumers.",
          "Read the latest decision in Scaling. For a hands-on experiment, take manual control, select a count and release it to return control to the predictor.",
          "Stop & drain, then review the exact result and recorded decisions in Sessions.",
        ],
        command: { label: "Prepare once, then reopen the console", text: "./streamscale.sh predictive-prepare\n./streamscale.sh console" },
        note: "Preparation needs at least 5 GiB free and is a substantial live campaign. Old three-partition models require preparation again. Historical charts and a selectable mode do not prove a compatible model is prepared on this machine. Read the live mode status and model identity.",
      },
      {
        title: "What the predictor sees",
        paragraphs: ["Quantile HistGradientBoostingRegressor models use 60 seconds of observed traffic history to predict per-partition arrivals over 10, 20 and 30 seconds. They estimate the median and upper quantiles. Future schedules, scenario names and seeds never enter model features.", "Every five seconds the controller projects partition backlog for every replica count from one to twelve. It uses separately measured capacity at each count, startup and rebalance delays, assignment constraints and database limits. It chooses the smallest capacity expected to satisfy the target; the queue-delay projection is a control estimate, not an exact p95 guarantee.", "The default policy looks ahead 20 seconds using the 90th-percentile forecast, 15% capacity headroom and 60 seconds of scale-down stabilization. The chart places horizon-mean forecasts at their future interval ends and compares them with actual means over the same intervals."],
      },
      {
        title: "Understand the status",
        table: {
          headings: ["Status", "Meaning"],
          rows: [
            ["Warming up", "A new run or agent restart needs fresh observed history."],
            ["Predicting", "A compatible model and fresh history support predictive decisions."],
            ["Fallback / hold", "Fresh lag and calibration can support reactive control. Missing calibration or stale measurements hold replicas; read the decision reason."],
            ["Manual override", "Predictive writes are suspended while you choose the replica count."],
            ["Capacity limited", "Available capacity is not expected to meet the objective. More replicas may not solve skew or a shared database bottleneck."],
          ],
        },
        note: "A history-only model cannot anticipate an unprecedented surge before it leaves any observable signal. The latency target is an objective, not a guarantee.",
      },
      {
        title: "Where Bayesian optimization fits",
        paragraphs: ["Ax/BoTorch uses Gaussian-process Bayesian optimization to tune policy settings offline between live experiments, not during a sale. It evaluates twelve candidate policies on the same two tuning workloads, minimizing replica-seconds subject to exact reconciled p95 ≤ 2 seconds and correctness passing.", "Training, validation, tuning and the separate 36-run evaluation use distinct runs. If no candidate is feasible, preparation keeps the conservative default and reports infeasibility. Compatible completed captures and artifacts can be reused; interrupted work resumes at completed-run boundaries."],
      },
      {
        title: "Safe manual control",
        paragraphs: ["Manual override first suspends the current autoscaler, then sets replicas. Release restores that strategy. Mode changes and writes are serialized so old predictive iterations cannot compete with HPA, KEDA or your override.", "Wait for the transition to finish before judging capacity. If a transition is failed or interrupted, read its error and use Retry. Automatic scaling decisions are ordinary controller behavior; changing the user's policy during a run makes it unsuitable for a formal comparison."],
      },
    ],
  },
  {
    id: "architecture",
    label: "How it works",
    title: "From a generated event to a durable order",
    summary: "The real components, delivery guarantees and evidence behind the visualization.",
    keywords: ["technical", "architecture", "Kafka", "SQL", "PostgreSQL", "DLQ", "offset", "transaction", "duplicate", "late", "partition", "correctness", "agent", "metrics"],
    sections: [
      {
        title: "The pipeline",
        paragraphs: ["Seeded publisher → Kafka orders.events.v1 (twelve partitions) → consumer group → PostgreSQL order_event and order_state. Invalid records go to orders.events.dlq.v1 with their source identity and reason.", "The same seed produces the same logical event sequence. Partition keys preserve per-order routing. Each execution gets a new run ID and consumer group, while earlier source ranges and artifacts remain available."],
        table: {
          headings: ["Store term", "System component"],
          rows: [
            ["Store doors", "Interactive publisher"],
            ["Checkout lanes", "Twelve Kafka partitions"],
            ["Tickets issued / now serving / queue", "High watermark / committed offset / lag"],
            ["Cashiers", "Consumer pods and their partition assignments"],
            ["Floor manager", "Scaling controller"],
            ["Sales ledger", "PostgreSQL event history and latest order state"],
            ["Problem-items desk", "Dead-letter queue"],
          ],
        },
      },
      {
        title: "Commit the effect before the offset",
        paragraphs: ["A consumer validates each event, writes its durable SQL effect in a transaction, then manually commits its source offset. If it crashes after the SQL commit but before the offset commit, Kafka can replay the event. Event identity makes the SQL write idempotent, so replay does not create a second fact.", "Latest order state is protected against late-event regression. Invalid records are acknowledged by the dead-letter topic before the source offset advances. A crash after that acknowledgement can create a second DLQ copy, so reconciliation tracks unique source records as well as copy counts."],
        note: "Delivery is at least once. Idempotent storage gives one durable SQL fact per event; it does not make every side effect exactly once.",
      },
      {
        title: "What reconciliation proves",
        bullets: [
          "Every valid unique source event has exactly one SQL fact, with no unexpected facts.",
          "Order state agrees with the valid source history and does not regress on late input.",
          "Invalid source records are represented in the DLQ with the expected reasons.",
          "The captured source range drained and offsets reached its end.",
        ],
        paragraphs: ["Reconciliation reads the captured Kafka range rather than trusting publisher intent. Correctness and latency are separate results: a correct run can still miss its p95 objective. If retention removed source records, an old run cannot be newly proven from those missing records."],
      },
      {
        title: "Where live measurements come from",
        paragraphs: ["The browser connects to a loopback Python server. Its in-cluster agent samples Kafka offsets and group membership, SQL, Kubernetes and consumer metrics. That agent owns sales, predictive decisions and fault recovery independently of the browser.", "Each source has its own health and timestamp. A failed source is unknown, not zero. Live requires an open browser connection, a reachable agent and fresh samples. Check Connection & diagnostics for source-level errors and Grafana/Prometheus links.", "Console sessions use their own release, group and run directory. The workflow lock prevents another project campaign or deployment from publishing into an active source range. The older measured campaign is retained separately."],
      },
      {
        title: "Technical locations",
        table: {
          headings: ["Repository location", "Responsibility"],
          rows: [
            ["src/streamscale/", "Event contract, generator, publisher, consumer, SQL and predictive models/controller."],
            ["console/server/streamscale_console/", "Local API, derived telemetry, operations and journal."],
            ["console/worker/streamscale_console_worker/", "Cluster agent, observation, scoped Kubernetes operations and sale execution."],
            ["console/web/", "React/TypeScript console and browser checks."],
            ["artifacts/console/ and artifacts/predictive/", "Local session evidence, campaign checkpoints and model/policy provenance."],
            ["docs/results/current/", "Retained historical M7 report, figures and measured results."],
          ],
        },
        note: "Predictive sessions retain ml-predictive in JSON configuration and metrics. The legacy SQL experiment_run.mode category uses fixed; for the actual policy read configuration.experiment_configuration.mode.",
      },
    ],
  },
  {
    id: "commands",
    label: "Commands & lifecycle",
    title: "Run, verify, stop and come back later",
    summary: "Copy a focused command instead of rerunning the complete campaign.",
    keywords: ["terminal", "launcher", "CLI", "start", "stop", "shutdown", "resume", "Docker", "pods", "install", "ports", "commands", "verify", "checks"],
    sections: [
      {
        title: "Start or reopen",
        command: { label: "Deploy a fresh cluster execution, then open the console", text: "./streamscale.sh cluster\n./streamscale.sh console" },
        paragraphs: ["If the pipeline is already deployed and running, console alone is enough. cluster creates a fresh execution and preserves earlier artifacts. The command prints the login URL; keep its terminal open."],
      },
      {
        title: "Resume retained services",
        command: { label: "Resume, then reopen the console", text: "./streamscale.sh resume\n./streamscale.sh console" },
        paragraphs: ["resume starts retained local services and kind nodes without creating a new run. Process counters reset after restarts, so run cluster before a strict fresh-run metrics check."],
      },
      {
        title: "Stop without losing your work",
        command: { label: "Stop this project's services", text: "./streamscale.sh stop" },
        paragraphs: ["Press Ctrl+C in the console/dashboard terminal to close that server and its forwards. Then run stop in a terminal at the repository root to stop project services and the kind node containing the pods. Volumes, containers and artifacts are retained.", "For only one stack, use ./streamscale.sh stop-local or ./streamscale.sh stop-cluster. Shutdown targets only the streamscale-local Compose project and kind nodes labelled io.x-k8s.kind.cluster=streamscale."],
        note: "Ctrl+C on the console alone leaves the cluster agent, active sale and recovery timers running. Use Stop & drain in Sale first if you want a completed session before shutting down services.",
      },
      {
        title: "ML preparation and evaluation",
        command: { label: "Prepare, verify, demonstrate and evaluate ML Predictive", text: "./streamscale.sh predictive-prepare\n./streamscale.sh predictive-check\n./streamscale.sh predictive-demo\n./streamscale.sh predictive-evaluate" },
        paragraphs: ["Run these with the console terminal closed and no active sale. Preparation calibrates capacity and collects training, validation and tuning traces. check verifies the live controller, overrides and recovery. demo runs one reconciled representative pilot. evaluate runs or resumes the separate 36-run held-out campaign.", "Keep at least 5 GiB free before starting preparation. Compatible captures and evidence are reused. These commands do real work and may take hours; they do not delete older data."],
      },
      {
        title: "Check what is running",
        command: { label: "Inspect service status", text: "./streamscale.sh status" },
        paragraphs: ["Use ./streamscale.sh verify to verify the current fresh cluster execution without republishing. Use ./streamscale.sh ui for dashboard access only; Grafana credentials are saved in artifacts/k8s/credentials.json."],
      },
      {
        title: "Run acceptance checks",
        command: { label: "Pipeline checks and console acceptance", text: "./streamscale.sh check\n./streamscale.sh console-check" },
        paragraphs: ["check includes unit, lint and live integration checks. console-check includes real sales, scaling transitions, bounded faults, replay and Chrome acceptance. These checks exercise the running system; they are more than a static UI build."],
      },
      {
        title: "Run the complete maintained workflow",
        command: { label: "All stages, without opening the console afterwards", text: "./streamscale.sh all --no-ui" },
        paragraphs: ["all includes setup, checks, fresh baseline executions, the historical experiments/report, console acceptance, predictive preparation and held-out evaluation. Its first execution can take several hours. Omit --no-ui to open the console at the end. Earlier artifacts are preserved and compatible completed campaigns are reused."],
      },
      {
        title: "Choose ports",
        command: { label: "Let the operating system select available ports", text: "./streamscale.sh console --port 0 --grafana-port 0 --prometheus-port 0" },
        paragraphs: ["Preferred ports are 18080 for the console, 13000 for Grafana and 19090 for Prometheus. Busy defaults select alternatives; open the actual URLs printed in the terminal. Use ./streamscale.sh help for all launcher options."],
      },
    ],
  },
  {
    id: "troubleshooting",
    label: "Troubleshooting",
    title: "Find the cause before repeating a run",
    summary: "Common observations, what they mean and the smallest useful next step.",
    keywords: ["error", "stale", "unreachable", "disconnected", "login", "backlog", "drain", "crash", "disk", "missing", "failed", "retention", "guard", "lock", "troubleshoot"],
    sections: [
      {
        title: "Disconnected, stale or agent unreachable",
        paragraphs: ["Open Connection & diagnostics to identify the failing source. Check ./streamscale.sh status. After resume, give the agent a few seconds to become ready. The console automatically retries its agent forward; errors are recorded in artifacts/console/agent-forward.log.", "A missing measurement is shown as —. Do not read it as zero traffic or a healthy empty queue. If the browser lost its server connection, reopen the exact login URL printed by the console command."],
        command: { label: "Inspect the project's running resources", text: "./streamscale.sh status" },
      },
      {
        title: "Login page or pipeline not deployed",
        paragraphs: ["Use the full token-bearing URL printed by ./streamscale.sh console; the running server also records it in artifacts/console/server.json. If deployment reports that the pipeline is not deployed, close the console command and run cluster before opening it again."],
      },
      {
        title: "The queue grows or will not drain",
        paragraphs: ["Compare arrivals with processing first. If arrivals are higher, wait for the surge to end, stop production or add consumers. Check desired, ready and assigned consumer counts separately; startup and rebalancing take time.", "If consumers are ready but throughput stays low, inspect database latency, CPU restrictions and active faults. Twelve consumers cannot overcome every database bottleneck or a heavily skewed partition. Recover pipeline ends active faults; Stop & drain ends new production."],
        action: { label: "Inspect live measurements", page: "overview" },
      },
      {
        title: "ML has no forecast or holds capacity",
        paragraphs: ["Read the decision reason in Scaling. A new run needs 60 seconds of fresh history. Missing calibration or stale observations holds capacity; a compatible prepared model with fresh observations can use reactive fallback while history warms up.", "Run predictive-prepare if a compatible artifact is missing. Close the console server first and allow any active sale to finish draining. Preparation is refused when less than 5 GiB is free. It preserves data rather than deleting evidence to make room."],
        action: { label: "Read the predictive status", page: "scaling" },
      },
      {
        title: "A workflow lock prevents a command",
        paragraphs: ["A console sale, deployment or campaign owns the cluster workflow so independent publishers cannot contaminate each other's source ranges. Let the owner finish; stop and drain an interactive sale, then close its console terminal before starting a campaign. Do not remove the lock while its owner is working."],
      },
      {
        title: "An older run no longer reconciles",
        paragraphs: ["Kafka retention can remove the captured source records required to verify an older execution. The launcher may stop at previous_drained with Captured source records are no longer available. Retained SQL and charts do not recreate that source evidence.", "Keep the original artifacts and the error log. Repeating verification cannot restore expired records. The guard deliberately stops progression rather than marking that historical run as proven; investigate that run before replacing its execution."],
      },
      {
        title: "A fault persists or a scaling transition failed",
        paragraphs: ["Use Recover pipeline to end active faults early. Fault timers survive a closed browser or agent restart and restore their bounded scope. For a failed/interrupted scaling transition, read the operation error and use Reapply after the infrastructure is healthy.", "Crash and OOM experiments are only marked verified after their observed outcome is confirmed. An injected command succeeding is not, by itself, proof of the expected crash or replay."],
        action: { label: "Review fault recovery", page: "faults" },
      },
    ],
  },
  {
    id: "guided",
    label: "Guided demos",
    title: "Learn by running a demonstration",
    summary: "Step through overload, manual rescue, autoscaling, database limits and crash recovery.",
    keywords: ["guide", "walkthrough", "demonstration", "manual rescue", "no scaling", "cpu versus lag", "database bottleneck", "crash and replay", "glossary"],
    sections: [
      {
        title: "Real actions, one step at a time",
        paragraphs: ["Choose a demonstration below, run its current action and watch the live system before moving on. These buttons control the real pipeline. Your selected demo and step stay in place when you visit another topic or page."],
      },
    ],
  },
];

export function searchDocs(query: string): DocArticle[] {
  const words = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  if (!words.length) return DOC_ARTICLES;
  return DOC_ARTICLES.filter((article) => {
    const content = JSON.stringify(article).toLocaleLowerCase();
    return words.every((word) => content.includes(word));
  });
}
