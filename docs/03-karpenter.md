# Karpenter

Karpenter watches for unschedulable pods and provisions EC2 nodes that fit their requirements. When pods are gone, it terminates the nodes.

## How it works

```
Pod created → unschedulable (no node fits) 
  → Karpenter sees pending pod 
  → evaluates NodePool constraints 
  → launches EC2 instance 
  → pod schedules → runs

Pod deleted → node goes idle 
  → Karpenter consolidates 
  → terminates unused node
```

## What gets created by Terraform

| Resource | Purpose |
|---|---|
| IAM role (IRSA) | Karpenter controller permissions |
| IAM role (node) | EC2 instance profile for Karpenter nodes |
| SQS queue | Receives interruption events (Spot, rebalance) |
| EventBridge rules | Pushes interruption/state events to SQS |

## Install via Helm

```bash
# add the Karpenter Helm repo
helm repo add karpenter https://charts.karpenter.sh/
helm repo update

# export Terraform outputs as variables
export CLUSTER_NAME=$(cd terraform && terraform output -raw cluster_name)
export CLUSTER_ENDPOINT=$(cd terraform && terraform output -raw cluster_endpoint)
export KARPENTER_IAM_ROLE_ARN=$(cd terraform && terraform output -raw karpenter_irsa_role_arn)
export KARPENTER_SQS_QUEUE=$(cd terraform && terraform output -raw karpenter_sqs_queue_name)

# install
helm upgrade --install karpenter oci://public.ecr.aws/karpenter/karpenter \
  --version "1.15.0" \
  --namespace kube-system \
  --set "settings.clusterName=${CLUSTER_NAME}" \
  --set "settings.clusterEndpoint=${CLUSTER_ENDPOINT}" \
  --set "settings.interruptionQueue=${KARPENTER_SQS_QUEUE}" \
  --set "serviceAccount.annotations.eks\.amazonaws\.com/role-arn=${KARPENTER_IAM_ROLE_ARN}" \
  --wait
```

Verify:

```bash
kubectl get pods -n kube-system -l app.kubernetes.io/name=karpenter
```

## Apply NodePool and EC2NodeClass

```bash
kubectl apply -f manifests/karpenter/ec2nodeclass.yaml
kubectl apply -f manifests/karpenter/nodepool.yaml
```

## EC2NodeClass

Defines the EC2-level configuration for nodes Karpenter launches.

Key fields:
- `amiFamily` — which AMI type (`AL2023` recommended for EKS 1.31+)
- `subnetSelectorTerms` — discovers subnets via the `karpenter.sh/discovery` tag
- `securityGroupSelectorTerms` — same tag-based discovery
- `instanceProfile` — the IAM instance profile from Terraform

See [manifests/karpenter/ec2nodeclass.yaml](../manifests/karpenter/ec2nodeclass.yaml).

## NodePool

Defines what kind of nodes Karpenter can provision.

Key fields:
- `requirements` — instance families, sizes, capacity types (on-demand vs spot)
- `limits` — max total CPU/memory (cost guard)
- `disruption` — consolidation policy (when to terminate idle nodes)

See [manifests/karpenter/nodepool.yaml](../manifests/karpenter/nodepool.yaml).

## Validation with nginx

The validation manifest uses nginx instead of a pause container because it proves
more things at once: Karpenter provisions the node, the image pulls correctly, DNS
resolves, and the pod is actually serving traffic.

The `nodeSelector: nodepool=default` is the key — it forces pods onto Karpenter
nodes only (the NodePool sets this label on every node it launches), so they can
never land on the system managed node group.

```bash
kubectl apply -f manifests/karpenter/validate-nginx.yaml
```

Watch Karpenter provision a node (takes ~60-90s on first run):

```bash
# terminal 1 — watch a new node appear
kubectl get nodes -w

# terminal 2 — watch pods go from Pending to Running
kubectl get pods -l app=karpenter-validate -w
```

Expected sequence:

```
# kubectl get pods -w
NAME                                  READY   STATUS    NODE
karpenter-validate-xxx-aaa            0/1     Pending   <none>   <- no node yet
karpenter-validate-xxx-bbb            0/1     Pending   <none>
karpenter-validate-xxx-ccc            0/1     Pending   <none>
karpenter-validate-xxx-aaa            0/1     Pending   <node>   <- Karpenter assigned
karpenter-validate-xxx-aaa            1/1     Running   <node>   <- pod up
...
```

Verify nginx is actually serving:

```bash
kubectl port-forward svc/karpenter-validate 8080:80 &
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080
# expected: 200
```

Clean up and watch the node disappear:

```bash
kubectl delete -f manifests/karpenter/validate-nginx.yaml

# Karpenter consolidates after 30s (consolidateAfter in nodepool.yaml)
kubectl get nodes -w
```
