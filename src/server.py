from fastmcp import FastMCP
from typing import List, Dict
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

mcp = FastMCP(name="k8s-mcp-agent", instructions="get_pods")

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

@mcp.tool()
def get_pods() -> List[Dict]:
    return list_pods_k8s()
@mcp.tool()
def describe_deployment_k8s(name: str, namespace: str):
    config.load_kube_config()
    api_instance = client.AppsV1Api()
    try:
        deployment = api_instance.read_namespaced_deployment(
            name=name, 
            namespace=namespace
        )
        return deployment
    except ApiException as e:
        print(f"Exception when calling AppsV1Api->read_namespaced_deployment: {e}")
        return None
if __name__ == "__main__":
    mcp.run()