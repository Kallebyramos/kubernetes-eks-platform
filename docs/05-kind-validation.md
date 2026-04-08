# Local Validation with KinD

KinD (Kubernetes in Docker) lets you run a real Kubernetes cluster locally inside
Docker containers. Use it to validate concepts without spending money on AWS.

## What you can and cannot validate here

| Component | KinD? | Notes |
|---|---|---|
| Spark Operator installation | yes | Full Helm install works |
| SparkApplication lifecycle | yes | Driver + executor pods, logs, status |
| Spark RBAC (service account) | yes | Same as EKS |
| Karpenter node provisioning | no | Needs real EC2 API |
| IRSA / IAM roles | no | Needs EKS OIDC |
| Spot interruption handling | no | Needs real AWS events |
| NodePool / EC2NodeClass CRDs | inspect only | Can install CRDs, but controller won't work |

The Spark Operator side is 100% reproducible locally. Karpenter is AWS-only.

---

## Prerequisites

```bash
# Docker (must be running)
docker info

# kind
kind version   # https://kind.sigs.k8s.io/docs/user/quick-start/#installation

# kubectl
kubectl version --client

# helm
helm version
```

---

## Step 1 — Create the KinD cluster

```bash
kind create cluster --config kind/cluster.yaml
```

This creates a cluster named `spark-lab` with 1 control-plane node and 2 workers.
KinD automatically updates your kubeconfig and sets the context.

Verify:

```bash
kubectl get nodes
# NAME                      STATUS   ROLES           AGE
# spark-lab-control-plane   Ready    control-plane   1m
# spark-lab-worker          Ready    <none>          1m
# spark-lab-worker2         Ready    <none>          1m
```

---

## Step 2 — Install the Spark Operator

```bash
helm repo add spark-operator https://kubeflow.github.io/spark-operator
helm repo update

helm upgrade --install spark-operator spark-operator/spark-operator \
  --namespace spark-operator \
  --create-namespace \
  --set webhook.enable=true \
  --wait
```

The `--wait` flag blocks until all pods are Ready. This can take 1-2 minutes on
the first run while Docker pulls the operator image.

Verify:

```bash
kubectl get pods -n spark-operator
# NAME                                        READY   STATUS    RESTARTS
# spark-operator-xxxx-yyyy                    1/1     Running   0
```

---

## Step 3 — Create the Spark service account and RBAC

The driver pod runs as a service account that needs permission to create and manage
executor pods. On EKS you would create this in advance; the Spark Operator Helm chart
does not create it by default.

```bash
kubectl create serviceaccount spark -n default

kubectl create clusterrolebinding spark-role \
  --clusterrole=edit \
  --serviceaccount=default:spark \
  --namespace=default
```

> The `edit` ClusterRole is a built-in Kubernetes role that grants read/write access
> to most resource types in a namespace. In production you would create a tighter Role,
> but for local validation this is fine.

---

## Step 4 — Run the Spark Pi job

The KinD manifest uses reduced resources (`512m` memory, 1 executor) to fit
comfortably on a local machine.

```bash
kubectl apply -f manifests/spark/example-app-kind.yaml
```

Watch the job progress in separate terminals:

```bash
# terminal 1 — watch SparkApplication status transitions
kubectl get sparkapplication -w

# terminal 2 — watch pods appear and disappear
kubectl get pods -w

# terminal 3 — follow driver logs once the pod is Running
kubectl logs -f spark-pi-driver
```

### Expected status transitions

```
spark-pi   SUBMITTED   ...
spark-pi   RUNNING     ...
spark-pi   SUCCEEDING  ...
spark-pi   COMPLETED   ...
```

### Expected driver log output

```
Pi is roughly 3.14159...
```

---

## Step 5 — Inspect the objects

While the job runs (or after it completes), explore the Kubernetes objects:

```bash
# full SparkApplication spec + status
kubectl describe sparkapplication spark-pi

# executor pod spec (notice: created by the driver, not the operator directly)
kubectl describe pod spark-pi-exec-1

# events on the namespace
kubectl get events --sort-by='.lastTimestamp'
```

Key things to notice:
- The driver pod is created by the Spark Operator
- The executor pods are created by the driver (via Kubernetes API)
- The `spark` service account is what allows the driver to call the API
- `SparkApplication.status.applicationState.state` tracks the full lifecycle

---

## Step 6 — Clean up

```bash
# delete the job (also removes driver/executor pods)
kubectl delete sparkapplication spark-pi

# verify pods are gone
kubectl get pods
```

---

## Comparing KinD vs EKS behavior

| Behavior | KinD | EKS + Karpenter |
|---|---|---|
| Where do nodes come from? | Docker containers, fixed at cluster creation | EC2 instances, provisioned on demand |
| What happens when pods are Pending? | Stay Pending if no capacity | Karpenter provisions a node |
| What happens when job finishes? | Nodes stay running | Karpenter terminates idle nodes |
| Cost | Free (local Docker) | Pay per EC2 instance-hour |
| Image pull | From Docker Hub through host | From Docker Hub or ECR |

On KinD the nodes are always there (2 workers). On EKS they appear and disappear.
The Spark job lifecycle itself (operator, driver, executor) is identical.

---

## Optional: inspect Karpenter CRDs (without real provisioning)

You can install Karpenter's CRDs to explore the API objects, even though the
controller cannot provision real nodes:

```bash
helm upgrade --install karpenter oci://public.ecr.aws/karpenter/karpenter \
  --version "1.3.3" \
  --namespace kube-system \
  --set "settings.clusterName=spark-lab" \
  --set "settings.clusterEndpoint=https://fake" \
  --set "controller.resources.requests.cpu=100m" \
  --set "controller.resources.requests.memory=256Mi"
# the controller pod will fail (no AWS creds), but CRDs are installed

# apply and inspect the manifests
kubectl apply -f manifests/karpenter/ec2nodeclass.yaml
kubectl apply -f manifests/karpenter/nodepool.yaml

kubectl get nodepool
kubectl get ec2nodeclass
kubectl describe nodepool default
```

This lets you explore the NodePool and EC2NodeClass structure without any AWS access.

---

## Tear down

```bash
kind delete cluster --name spark-lab
```

This removes the entire cluster and all Docker containers. No state is left behind.
