# Supervisor (`optics supervise`)

The supervisor is a scaled, multi-worker front tier for the optics HTTP API.
It sits **in front of** copies of the same app `optics serve` runs
(`optics_framework.common.expose_api:app`), forwards whole keyword calls, and
routes each session to the worker that owns it.

`optics serve` is unchanged and remains the way to run a single, unsupervised
server. Use the supervisor when you need more than one worker process,
more than one supervisor replica, or one isolated worker per session.

```bash
optics supervise                          # fixed pool of 2 local workers (like PR #202)
optics supervise --workers 4              # bigger pool
optics supervise --store redis --redis-url redis://localhost:6379/0
optics supervise --worker-mode per_session
optics supervise --worker-mode per_session --launcher docker
optics supervise --max-sessions 8
```

The standalone script path still works too:
`python tools/supervisor_app/supervisor_tool.py --workers 2` (a shim around
`optics_framework.helper.supervisor`).

## Scaling modes

Three independent axes, each defaulting to the original behavior. Together
the defaults reproduce PR #202 exactly.

### 1. Routing store — `--store` / `SUPERVISOR_STORE`

| Value | Meaning |
|---|---|
| `memory` (default) | Routing state lives in the supervisor process. Single supervisor only. |
| `redis` | Routes, the live-worker registry, and session leases live in Redis. Run **N stateless supervisor replicas** behind any load balancer (no session affinity needed): any replica routes any session, and losing a replica loses nothing. Needs the optional dependency: `pip install optics-framework[supervisor]`. |

### 2. Worker topology — `--worker-mode` / `SUPERVISOR_WORKER_MODE`

| Value | Meaning |
|---|---|
| `pool` (default) | A fixed pool of workers started up front (`--workers`, `--base-port`); sessions share workers. |
| `per_session` | **One worker per session**, launched on `POST /v1/sessions/start` and destroyed on stop, crash, or idle expiry. Sessions never share a process: a slow keyword or a crash in one session cannot affect another. |

In `per_session` mode every session holds a **lease** that is renewed by its
own traffic. A reaper reclaims the worker of any session whose lease expires
(crashed client, dropped connection, dead supervisor). A worker that dies
mid-session is **not** recovered: the next request returns 502 and the session
is lost — recreate it.

### 3. Worker launcher — `--launcher` / `SUPERVISOR_LAUNCHER` (per-session mode)

| Value | Meaning |
|---|---|
| `subprocess` (default) | Local `uvicorn` process per session on an ephemeral port. |
| `docker` | One container per session, addressed by its subnet IP (`docker` CLI must be available). |
| `k8s` | One pod per session, addressed by its pod IP (`kubectl` must be configured). |

## Flags and environment variables

CLI flags map 1:1 to env vars. Resolution order: **flag > env var > default.**

| Flag | Env var | Default | Purpose |
|---|---|---|---|
| `--store` | `SUPERVISOR_STORE` | `memory` | Routing/lease store backend |
| `--redis-url` | `SUPERVISOR_REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis address when store=redis |
| `--worker-mode` | `SUPERVISOR_WORKER_MODE` | `pool` | Fixed pool vs one worker per session |
| `--launcher` | `SUPERVISOR_LAUNCHER` | `subprocess` | How per-session workers run |
| `--max-sessions` | `SUPERVISOR_MAX_SESSIONS` | unlimited | Admission cap (429 at the cap) |
| `--workers` | — | `2` | Pool size (pool mode only) |
| `--base-port` | — | `9000` | First pool worker port (pool mode only) |
| — | `SUPERVISOR_STARTUP_TIMEOUT_S` | `30` | Per-session worker readiness timeout |
| — | `SUPERVISOR_LEASE_TTL_S` | `120` | Idle time before a session is reaped |
| — | `SUPERVISOR_REAP_INTERVAL_S` | `5` | Reaper sweep interval |
| — | `SUPERVISOR_WORKER_TTL_S` | `10` | Worker-registry heartbeat TTL |
| — | `SUPERVISOR_WORKER_APP` | `optics_framework.common.expose_api:app` | ASGI app workers run (test seam) |
| — | `SUPERVISOR_WORKER_IMAGE` | — | Worker image (docker/k8s, required) |
| — | `SUPERVISOR_WORKER_NETWORK` | — | Docker network |
| — | `SUPERVISOR_WORKER_RESOURCES` | — | Extra `docker run` args, e.g. `--memory=1g` |
| — | `SUPERVISOR_WORKER_PORT` | `8000` | Port the worker image listens on |
| — | `SUPERVISOR_WORKER_NAMESPACE` | `default` | Kubernetes namespace for session pods |
| — | `SUPERVISOR_WORKER_DEADLINE_S` | off | Pod `activeDeadlineSeconds` backstop |

## The session cap is the device cap

These upgrades multiply **session/API throughput, not devices** — one worker
is still one session on one device slot. Set `SUPERVISOR_MAX_SESSIONS` to the
Appium hub's parallel-session/device-slot count; at the cap
`POST /v1/sessions/start` returns 429 instead of launching a worker that would
only queue or fail at the hub.

## Deployment

Provider-agnostic Kubernetes manifests (stateless supervisor Deployment +
Service, dev Redis, RBAC, reference worker Pod) live in
[`tools/supervisor_app/deploy/`](https://github.com/mozarkai/optics-framework/tree/main/tools/supervisor_app/deploy)
with a README describing the topology, the double reaping timers, and what a
worker pod needs.

## Related

- [PR #202 — the original supervisor](https://github.com/mozarkai/optics-framework/pull/202)
