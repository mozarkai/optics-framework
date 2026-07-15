# Supervisor deployment (reference, provider-agnostic)

Raw Kubernetes manifests for the scaled supervisor topology:

```
        L7 load balancer / ingress   (no session affinity — routing lives in the store)
                    │
        ┌───────────┴───────────┐
   supervisor replica 1 ... N    (stateless Deployment: supervisor.yaml)
        │            shared store (Redis): routes + leases + worker registry (redis.yaml)
        └── Kubernetes API ────► session Pod (1 per session, launched by K8sLauncher)
                                      └── egress ──► Appium hub URL
```

| File | What it is |
|---|---|
| `supervisor.yaml` | Stateless supervisor `Deployment` + `Service`. Scale `replicas` freely; any replica routes any session via the shared store. |
| `redis.yaml` | **Dev-only** single-node Redis `StatefulSet` + `Service`. In production point `SUPERVISOR_REDIS_URL` at your platform's managed Redis instead. |
| `rbac.yaml` | `ServiceAccount` + `Role`/`RoleBinding` letting the supervisor create/get/list/delete session Pods in the worker namespace. |
| `worker-pod.yaml` | Reference Pod template documenting what a session worker needs: the optics worker image, a readiness/liveness probe on `GET /`, an `activeDeadlineSeconds` backstop, and outbound egress to the Appium hub. `K8sLauncher` creates pods programmatically (`kubectl run` + overrides); this file is the target shape, not something you `kubectl apply`. |

Nothing here is provider-specific: swap image registry, Redis endpoint, and
ingress for your platform's equivalents. The same shape works on Nomad, ECS,
or Docker Swarm behind the `WorkerLauncher` interface.

## What a worker pod needs — the whole list

1. The optics config containing the **Appium hub URL** (via Secret/ConfigMap).
2. **Outbound egress** to that hub URL — the only external dependency.
3. A readiness/liveness probe on `GET /` (the endpoint `wait_ready` polls).

No privileged pods, no in-cluster emulators, no device management — the hub
owns all of that.

## Reaping & cost control

Two independent timers, so no single failure leaks pods:

- The supervisor's **lease reaper** (`SUPERVISOR_LEASE_TTL_S`) deletes the pod
  of any session whose client went away.
- The pod-level **`activeDeadlineSeconds`** backstop
  (`SUPERVISOR_WORKER_DEADLINE_S`) kills an orphaned worker even if every
  reaper misses it.

## Concurrency ceiling

Set `SUPERVISOR_MAX_SESSIONS` to the Appium hub's parallel-session /
device-slot count. At the cap, `POST /v1/sessions/start` returns 429 instead
of launching a pod that would only queue or fail at the hub — pods are not the
bottleneck, devices are.
