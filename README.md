# k8s-mcp-agent

An [MCP](https://modelcontextprotocol.io/) server that exposes Kubernetes cluster inspection/operations as tools, plus a local client that lets a small [Ollama](https://ollama.com/) model (`qwen2.5:3b`) call those tools using natural language.

```
User prompt → Ollama (tool-calling LLM) → MCP Client → MCP Server (FastMCP) → Kubernetes API
```

The LLM never touches the cluster directly. It only decides *which* tool to call and with *what arguments*; the MCP server is the only thing that talks to Kubernetes, via the official `kubernetes` Python client.

---

## Project structure

```
.
├── src/
│   ├── server.py          # MCP server — defines the K8s tools
│   └── ollama_client.py   # Loads MCP tools into Ollama and runs tool-calling chat
├── requirements.txt       # Python dependencies
├── nginx-deployment.yaml  # Example Deployment manifest used to test the tools against
└── myenv/                 # Local virtual environment (not meant to be committed)
```

---

## How the two files work together

### `server.py` — the MCP server

Built with **FastMCP**, which turns plain Python functions into MCP tools automatically discoverable by any MCP-compatible client (Claude Desktop, Ollama via the client below, etc.). It authenticates to the cluster with `config.load_kube_config()` — i.e. it uses whatever context is active in your local `~/.kube/config` (same as `kubectl`).

Each tool is a thin `@mcp.tool()` wrapper around a "real" function that does the Kubernetes API work. Splitting them like this means the underlying logic is testable without spinning up MCP at all.

| Tool (exposed to the LLM) | Underlying function | What it does |
|---|---|---|
| `get_pods()` | `list_pods_k8s()` | Calls `CoreV1Api.list_pod_for_all_namespaces()`. Returns name, namespace, phase (`Running`/`Pending`/...), pod IP, and total container restart count for every pod in the cluster. |
| `describe_deployment(name, namespace)` | `describe_deployment_k8s()` | Calls `AppsV1Api.read_namespaced_deployment()`. Returns desired vs. actual replica counts (`desired_replicas`, `available_replicas`, `updated_replicas`, `ready_replicas`) and the container image(s) — the same numbers `kubectl get deploy` shows. |
| `get_logs(pod_name, namespace, container?, lines=100)` | `tail_logs_k8s()` | Calls `CoreV1Api.read_namespaced_pod_log()`. Equivalent to `kubectl logs <pod> -n <ns> --tail=100`. `container` is only needed if the pod runs more than one container. |
| `restart_rollout(name, namespace, confirm=False)` | `restart_rollout_k8s()` | Equivalent to `kubectl rollout restart deployment/<name>`. It patches the pod template's `spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"]` with the current timestamp — changing the pod template makes Kubernetes roll every pod over, without changing the image or config. **Guarded**: if `confirm` isn't explicitly `True`, it refuses and returns `requires_confirmation: True` instead of acting — a safety rail so an LLM can't restart a production deployment on a whim. |

All Kubernetes errors (`ApiException`) are caught and returned as `{"error": ..., "status": ...}` rather than crashing the server, so the LLM gets a structured response it can reason about (e.g. "deployment not found") instead of a stack trace.

The server runs as a standalone HTTP process: `mcp.run(transport="http", port=8080)`, exposing MCP over HTTP at `http://127.0.0.1:8080/mcp`.

### `ollama_client.py` — the tool-calling client

1. **`load_mcp_tools()`** connects to the running MCP server as a client, calls `mcp.list_tools()`, and converts each MCP tool definition into the JSON-schema tool format Ollama's `chat()` API expects (`{"type": "function", "function": {...}}`). This is the "translation layer" between MCP's tool schema and Ollama's.
2. **`ollama_chat()`** sends the user's prompt plus the translated tool list to the local model (`qwen2.5:3b`). If the model decides a tool is needed, Ollama returns a `tool_calls` entry; the function extracts the first call's name and arguments. If no tool is needed, it just returns the model's text answer.
3. The `__main__` block is a minimal smoke test: it hardcodes the prompt `"show me all Pods pls"`, loads the tools, and prints what the model decided to do.

**Note:** this client currently only *decides* which tool to call and prints that decision — it doesn't actually execute the tool against the MCP server and feed the result back to the model yet. A full agent loop would add a step that calls `mcp.call_tool(name, arguments)` and sends the result back to `ollama.chat()` so the model can produce a final natural-language answer.

---

## Kubernetes concepts used in this project

- **Cluster / API server** — everything above talks to the Kubernetes API server, the single entry point for all cluster operations (what `kubectl` itself talks to).
- **kubeconfig** (`~/.kube/config`) — holds cluster address, credentials, and the "current context." `config.load_kube_config()` reads this exact file, so whatever cluster `kubectl` is pointed at is the one this server manages.
- **Namespace** — a logical partition of the cluster (e.g. `default`, `kube-system`). Almost every call here is namespace-scoped except `list_pod_for_all_namespaces`, which deliberately spans all of them.
- **Pod** — the smallest deployable unit; one or more containers sharing network/storage. `status.phase` is one of `Pending`, `Running`, `Succeeded`, `Failed`, `Unknown`.
- **Deployment** — a controller that manages a set of identical Pods (via a ReplicaSet) and handles rolling updates. Key fields:
  - `spec.replicas` — *desired* pod count.
  - `status.available_replicas` / `ready_replicas` / `updated_replicas` — *actual* state; comparing these to `spec.replicas` tells you if a rollout is healthy or stuck.
- **Rollout restart** — Kubernetes has no native "restart" verb; rolling all pods over is achieved by changing something in the pod template (here, a timestamp annotation), which triggers a new ReplicaSet rollout with zero downtime (assuming `replicas` > 1).
- **Container restart count** — tracked per-container in `status.container_statuses[].restart_count`; a high number usually signals crash-looping.

---

## `nginx-deployment.yaml` explained

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-nginx
  labels:
    app: nginx
spec:
  replicas: 2
  selector:
    matchLabels:
      app: nginx
  template:
    metadata:
      labels:
        app: nginx
    spec:
      containers:
        - name: nginx
          image: nginx:1.25
```

Line by line:

- **`apiVersion: apps/v1`** — Deployments live in the `apps` API group, version `v1`.
- **`kind: Deployment`** — tells Kubernetes to create a Deployment controller (as opposed to a bare Pod, a StatefulSet, a DaemonSet, etc.).
- **`metadata.name: my-nginx`** — the Deployment's own name, used when you run `kubectl get deployment my-nginx` or call `describe_deployment("my-nginx", "<namespace>")` from this project.
- **`metadata.labels.app: nginx`** — a label on the Deployment object itself (mostly for humans/tooling to query by; not functionally required).
- **`spec.replicas: 2`** — the desired number of Pods. This is the number `describe_deployment` reports as `desired_replicas`.
- **`spec.selector.matchLabels.app: nginx`** — **this is the critical link.** The Deployment "owns" (and will scale/replace) any Pod whose labels match this selector. It **must** match `spec.template.metadata.labels` below, or Kubernetes rejects the manifest.
- **`spec.template`** — the Pod template. Every Pod the Deployment creates is stamped from this. It has its own `metadata` (labels — must match the selector) and its own `spec` (the actual container list).
- **`spec.template.spec.containers[0]`**:
  - `name: nginx` — container name within the Pod.
  - `image: nginx:1.25` — the exact image tag to pull and run. Pinning to `1.25` instead of `latest` means restarts and rollouts always produce a predictable version.

Because no `strategy`, `resources`, `ports`, `livenessProbe`, or `readinessProbe` are set, Kubernetes uses defaults: `RollingUpdate` strategy, no resource limits/requests, and no health checks — fine for a quick test manifest, but worth adding before running anything real.

Apply it with:
```bash
kubectl apply -f nginx-deployment.yaml
kubectl get deployment my-nginx
kubectl get pods -l app=nginx
```

---

## Setup

```bash
python -m venv myenv
source myenv/bin/activate       # myenv\Scripts\activate on Windows
pip install -r requirements.txt

# make sure kubectl works against your cluster first:
kubectl get nodes

# make sure Ollama has the model pulled:
ollama pull qwen2.5:3b
```

Or simply run the provided `install.sh` if it already automates the steps above.

## Running

Terminal 1 — start the MCP server:
```bash
python src/server.py
```

Terminal 2 — run the Ollama client:
```bash
python src/ollama_client.py
```

---

## Suggested next steps

- Wire up `ollama_client.py` to actually **execute** the chosen tool via `mcp.call_tool()` and feed the result back into a second `ollama.chat()` call, closing the agent loop.
- Add a multi-turn loop instead of the single hardcoded prompt.
- Add RBAC scoping in the kubeconfig used by the server (read-only for inspection tools, separate elevated context for `restart_rollout`) since the tool currently inherits whatever permissions the local kubeconfig has.