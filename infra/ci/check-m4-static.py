#!/usr/bin/env python3
"""Validate the disposable M4 manifests without creating a cluster."""

import re
import subprocess
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[2]
K8S = ROOT / "infra/k8s"
FLUX = ROOT / "infra/flux"
NODE_IMAGE = "kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed"
MINIO_IMAGE = "ghcr.io/intikhab49/minio-from-source@sha256:1025a6dd8452e6fe8d3fdf16dbce7c18f8417acd22e5abd7376d8322aee5edab"
STATSD_IMAGE = "prom/statsd-exporter:v0.31.0@sha256:1d5d341c4eb7abdd3445a3f85c3d498402b676ad29b4cf0421f5d280943871ab"
SCHEMA_REF = "0c4dd99d9873f7b4c2da24c008476f5841adbe2d"


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def documents(text):
    return [doc for doc in yaml.load_all(text, Loader=UniqueLoader) if doc is not None]


def run(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def main():
    assert run("git", "rev-parse", "--show-toplevel").strip() == str(ROOT)
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "docker-compose.yaml", "infra/terraform"], cwd=ROOT, check=True)
    untracked = run("git", "ls-files", "--others", "--exclude-standard").splitlines()
    assert not any(p.startswith("infra/terraform/") or re.search(r"(^|/)(\.env[^/]*|kubeconfig[^/]*|[^/]*\.tfstate[^/]*)$", p) for p in untracked)

    kind = documents((ROOT / "infra/kind/cluster.yaml").read_text())[0]
    assert kind["nodes"] == [{"role": "control-plane", "image": NODE_IMAGE}]
    assert (ROOT / "observability/statsd-mappings.yml").read_bytes() == (K8S / "statsd/statsd-mappings.yml").read_bytes()

    flux_install = documents(run("flux", "install", "--export", "--components=source-controller,kustomize-controller"))
    crds = {(d["spec"]["group"], d["spec"]["names"]["kind"]): d for d in flux_install if d.get("kind") == "CustomResourceDefinition"}
    flux_resources = [d for p in FLUX.glob("*.yaml") for d in documents(p.read_text())]
    for resource in flux_resources:
        group, version = resource["apiVersion"].split("/")
        crd = crds[(group, resource["kind"])]
        schema = next(v["schema"]["openAPIV3Schema"] for v in crd["spec"]["versions"] if v["name"] == version)
        jsonschema.Draft7Validator(schema).validate(resource)
        if resource["kind"] == "GitRepository":
            assert resource["spec"]["url"] == "http://market-pulse-m4-git/fixture.git"

    rendered = []
    for overlay in (K8S / "statsd", K8S / "minio/namespace", K8S / "minio/app"):
        rendered += documents(run("kubectl", "kustomize", str(overlay)))
    rendered += documents((K8S / "minio/client.yaml").read_text())
    assert len(rendered) == 9, len(rendered)
    for resource in rendered:
        assert resource["kind"] != "Secret"
        assert resource["metadata"].get("namespace", resource["metadata"].get("name")) in {
            "market-pulse-gitops-pilot", "market-pulse-m4-storage"
        }
        for item in walk(resource):
            assert not any(key in item for key in ("hostPath", "hostNetwork", "hostPID", "hostPort", "privileged", "externalIPs"))
            if "image" in item:
                assert item["image"] in {MINIO_IMAGE, STATSD_IMAGE}, item["image"]
        if resource["kind"] == "Service":
            assert resource["spec"].get("type", "ClusterIP") == "ClusterIP"
            assert "nodePort" not in str(resource["spec"])
            ports = {p["port"] for p in resource["spec"]["ports"]}
            assert ports == ({9102, 9125} if resource["metadata"]["name"] == "statsd-exporter" else {9000})
        if resource["kind"] in {"Deployment", "StatefulSet"}:
            pod = resource["spec"]["template"]["spec"]
            assert pod["automountServiceAccountToken"] is False
            assert pod["securityContext"]["runAsNonRoot"] is True
            for container in pod["containers"]:
                assert all(container["resources"][bound][key] for bound in ("requests", "limits") for key in ("cpu", "memory"))
                assert "readinessProbe" in container and "livenessProbe" in container
                assert container["securityContext"]["allowPrivilegeEscalation"] is False
    minio = next(r for r in rendered if r["kind"] == "StatefulSet")
    assert minio["spec"]["persistentVolumeClaimRetentionPolicy"] == {"whenDeleted": "Retain", "whenScaled": "Retain"}
    assert len(minio["spec"]["volumeClaimTemplates"]) == 1
    claim = minio["spec"]["volumeClaimTemplates"][0]["spec"]
    assert claim["storageClassName"] == "standard" and claim["resources"]["requests"]["storage"] == "1Gi"
    for resource in (minio, next(r for r in rendered if r["kind"] == "Pod")):
        env = resource["spec"].get("template", resource)["spec"]["containers"][0]["env"]
        assert {e["name"] for e in env} == {"MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"}
        assert all(e["valueFrom"]["secretKeyRef"]["name"] == "minio-credentials" for e in env)
    assert next(r for r in rendered if r["kind"] == "Pod")["spec"]["automountServiceAccountToken"] is False

    schema_url = f"https://raw.githubusercontent.com/yannh/kubernetes-json-schema/{SCHEMA_REF}/{{{{.NormalizedKubernetesVersion}}}}-standalone-strict/{{{{.ResourceKind}}}}{{{{.KindSuffix}}}}.json"
    subprocess.run(["kubeconform", "-strict", "-summary", "-kubernetes-version", "1.36.4", "-schema-location", schema_url],
                   input=yaml.safe_dump_all(rendered), text=True, check=True, cwd=ROOT)

    for path in (ROOT / "docs/kubernetes-gitops-plan.md", ROOT / "infra/kind/README.md", FLUX / "README.md", K8S / "statsd/README.md", K8S / "minio/README.md"):
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", path.read_text()):
            if "://" not in target and not target.startswith("#"):
                assert (path.parent / target.split("#", 1)[0]).exists(), (path, target)
    print(f"M4 static checks passed: {len(flux_resources)} Flux resources, {len(rendered)} rendered Kubernetes resources, links, mapping, isolation")


if __name__ == "__main__":
    main()
