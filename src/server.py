from fastmcp import FastMCP
from typing import List, Dict
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from datetime import datetime, timezone


mcp = FastMCP(
    name="k8s-mcp-agent",
    instructions="Tools for inspecting pod and deployment status in a Kubernetes cluster.",
)

def list_pods_k8s() -> List[Dict]:
    config.load_kube_config()
    v1 = client.CoreV1Api()
    ret = v1.list_pod_for_all_namespaces(watch=False)

    pods = []
    for i in ret.items:
        pods.append({
            "name": i.metadata.name,
            "namespace": i.metadata.namespace,
            "status": i.status.phase,
            "pod_ip": i.status.pod_ip,
            "restarts": sum(cs.restart_count for cs in (i.status.container_statuses or [])),
        })
    return pods

def describe_deployment_k8s(name: str, namespace: str) -> Dict:
    config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    try:
        dep = apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
    except ApiException as e:
        return {"error": str(e), "status": e.status}

    spec = dep.spec
    status = dep.status
    containers = spec.template.spec.containers if spec.template and spec.template.spec else []

    return {
        "name": dep.metadata.name,
        "namespace": dep.metadata.namespace,
        "desired_replicas": spec.replicas,
        "available_replicas": status.available_replicas or 0,
        "updated_replicas": status.updated_replicas or 0,
        "ready_replicas": status.ready_replicas or 0,
        "images": [c.image for c in containers],
    }

def tail_logs_k8s(pod_name: str, namespace: str, container: str | None = None, lines: int = 100) -> str:
    config.load_kube_config()
    v1 = client.CoreV1Api()
    try:
        if container is not None :
            logs = v1.read_namespaced_pod_log(name=pod_name, namespace=namespace,container=container,tail_lines=lines)
        else:
            logs = v1.read_namespaced_pod_log(name=pod_name, namespace=namespace,tail_lines=lines)
        return logs
    except ApiException as e:
            return f"error ({e.status}): {e.reason}"

def restart_rollout_k8s(name: str, namespace: str, confirm: bool = False) -> dict:
    if confirm is not True:
        return {"error": "confirmation required", "requires_confirmation": True}
    else:
        config.load_kube_config()
        api  = client.AppsV1Api()
        k8s_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        patch_body = {
            "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": k8s_timestamp
                     }
                  }
                 }
             }
        }
        try:
            api.patch_namespaced_deployment(
                name=name,
                namespace=namespace,
                body=patch_body
            )
            return {"status": "restarted", "name":name, "namespace": namespace, "restarted_at": k8s_timestamp} 
        except ApiException as e:
                return {"error": str(e), "status": e.status}

@mcp.tool()
def get_logs(pod_name: str, namespace: str, container: str | None = None, lines: int = 100) -> str:
    """Return recent log lines for a pod's container (or its only container if none is specified)."""
    return tail_logs_k8s(pod_name, namespace, container, lines)
@mcp.tool()
def get_pods() -> List[Dict]:
    """List all pods across all namespaces, including their status, IP, and restart count."""
    return list_pods_k8s()
@mcp.tool()
def describe_deployment(name: str, namespace: str):
    """Get a Deployment's desired vs actual replica counts, readiness, and container image."""
    return describe_deployment_k8s(name, namespace);
@mcp.tool()
def restart_rollout(name: str, namespace: str, confirm: bool = False) -> dict:
    """
    Restart a deployment's rollout. Requires confirm=True to execute —
    without it, returns a message asking for explicit confirmation instead
    of performing the restart.
    """
    return restart_rollout_k8s(name, namespace, confirm)
if __name__ == "__main__":
    mcp.run(transport="http", port=8080)