# Study Guide: EKS + Karpenter + Spark Operator

This guide is based on the actual code in this repository. Each section explains
the *why* behind each decision, not just the *what*.

This project has two paths that work together:

| Path | Purpose | What runs |
|---|---|---|
| **Local (KinD)** | Learn and validate fast, zero cost | Spark Operator + scheduling flow |
| **Production (AWS EKS)** | Real infrastructure, real Karpenter | Everything, including node autoscaling |

Use the local path first. Switch to AWS when you need Karpenter or real IAM/IRSA.

---

## Table of Contents

1. [The Big Picture](#1-the-big-picture)
2. [Terraform & Infrastructure as Code](#2-terraform--infrastructure-as-code)
3. [VPC & Networking](#3-vpc--networking)
4. [Amazon EKS](#4-amazon-eks)
5. [IAM & IRSA](#5-iam--irsa)
6. [Karpenter](#6-karpenter)
   - [6.1 nginx Karpenter Validation](#61-nginx-karpenter-validation)
7. [Kubeflow Spark Operator](#7-kubeflow-spark-operator)
   - [7.1 Installing via Helm](#71-installing-the-spark-operator-via-helm)
   - [7.2 RBAC for the Spark Service Account](#72-rbac-for-the-spark-service-account)
   - [7.3 SparkApplication CRD — Full Anatomy](#73-sparkapplication-crd--full-anatomy)
   - [7.4 Job Lifecycle — Status Transitions](#74-job-lifecycle--status-transitions)
   - [7.5 Driver vs. Executor — Deep Dive](#75-driver-vs-executor--deep-dive)
   - [7.6 The Kubernetes Scheduling Flow](#76-the-kubernetes-scheduling-flow)
   - [7.7 cluster mode vs. client mode](#77-cluster-mode-vs-client-mode)
   - [7.8 Useful commands](#78-useful-commands)
8. [Validation Strategy — Local vs. Production](#8-validation-strategy--local-vs-production)
9. [How Everything Connects (End-to-End Flow)](#9-how-everything-connects-end-to-end-flow)
10. [CI/CD Pipeline](#10-cicd-pipeline)
    - [Workflow 1 — validate.yml](#workflow-1--validateyml-every-push--pr)
    - [Workflow 2 — integration.yml](#workflow-2--integrationyml-every-pr--manual)
    - [Workflow 3 — infra-pr-review.yml](#workflow-3--infra-pr-reviewyml-pr-touching-infrastructure)
    - [Workflow 4 — upgrade.yml](#workflow-4--upgradeyml-every-monday--manual)
11. [Cost Optimization Decisions](#11-cost-optimization-decisions)
12. [Key Concepts to Memorize](#12-key-concepts-to-memorize)

---

## 1. The Big Picture

This project builds a **data platform on Kubernetes** where:

- AWS EKS provides the Kubernetes control plane
- Karpenter automatically provisions and terminates EC2 nodes based on workload demand
- Kubeflow Spark Operator runs Apache Spark jobs as native Kubernetes resources

The core idea: **you only pay for what you use**. When a Spark job is submitted,
nodes appear. When the job finishes, nodes disappear. There is no idle cluster sitting
around burning money.

```
Developer submits SparkApplication manifest
        |
        v
Spark Operator creates Driver + Executor pods
        |
        v
Pods are Pending (no nodes available)
        |
        v
Karpenter detects Pending pods
        |
        v
Karpenter provisions EC2 instance (Spot preferred)
        |
        v
Pods run on new node, Spark job executes
        |
        v
Job finishes, pods are deleted
        |
        v
Karpenter sees idle node, terminates it after 30s
```

---

## 2. Terraform & Infrastructure as Code

### What is Terraform?

Terraform is a tool to declare cloud infrastructure in code. Instead of clicking
around the AWS console, you write `.tf` files that describe the desired state and
Terraform figures out what to create, update, or delete.

### How this project uses it

The `terraform/` directory contains all AWS infrastructure:

| File | What it creates |
|---|---|
| `main.tf` | Provider config, backend, shared data sources |
| `variables.tf` | All tunable parameters (cluster name, region, etc.) |
| `vpc.tf` | VPC, subnets, NAT gateway, route tables |
| `eks.tf` | EKS cluster, managed node group, access entries |
| `karpenter.tf` | IAM roles, SQS queue, EventBridge rules for Karpenter |
| `outputs.tf` | Values to use after apply (cluster endpoint, role ARNs) |

### Terraform workflow

```bash
terraform init      # Download providers and modules
terraform plan      # Show what will be created/changed/destroyed
terraform apply     # Actually make the changes
terraform destroy   # Tear everything down
```

### State file

Terraform tracks what it created in a **state file** (`terraform.tfstate`). This
project uses local state (file on disk). In production you would use an S3 backend
with DynamoDB locking so multiple people can collaborate safely.

```hcl
# main.tf - local backend (learning)
backend "local" {}

# Production would look like this:
# backend "s3" {
#   bucket         = "my-terraform-state"
#   key            = "eks/terraform.tfstate"
#   region         = "us-east-1"
#   dynamodb_table = "terraform-locks"
# }
```

### Modules

Rather than writing every AWS resource from scratch, this project uses community
modules from the Terraform Registry:

- `terraform-aws-modules/vpc/aws` — creates the entire VPC in ~20 lines
- `terraform-aws-modules/eks/aws` — creates EKS cluster, node groups, IRSA

Modules are wrappers around many `resource` blocks. They expose variables as inputs
and useful values as outputs.

---

## 3. VPC & Networking

### Why a custom VPC?

AWS gives you a default VPC, but it has no meaningful isolation and bad habits.
This project creates a dedicated VPC (`10.0.0.0/16`) that gives you full control.

### Subnet design

```
VPC: 10.0.0.0/16
  ├── Private Subnet AZ1: 10.0.1.0/24   <-- EKS nodes live here
  ├── Private Subnet AZ2: 10.0.2.0/24   <-- EKS nodes live here
  ├── Public Subnet AZ1:  10.0.101.0/24 <-- NAT gateway, future load balancers
  └── Public Subnet AZ2:  10.0.102.0/24 <-- NAT gateway, future load balancers
```

**Private subnets**: No direct internet access. Traffic goes through NAT gateway.
EKS nodes should always be private — you don't want random internet access to your
worker nodes.

**Public subnets**: Have an Internet Gateway route. Used for NAT and load balancers
that need a public IP.

**Two AZs**: Spreading across availability zones means one datacenter failure does
not take down everything. For a lab this is the minimum viable HA setup.

### NAT Gateway

Private subnet nodes need to reach the internet (to pull container images, call AWS
APIs). They cannot have public IPs, so traffic goes:

```
Private node --> NAT Gateway (in public subnet) --> Internet Gateway --> Internet
```

This project uses **one NAT gateway** (cost optimization for a lab). Production would
use one per AZ for resilience.

### Tags and Karpenter discovery

This is critical. Karpenter needs to know which subnets and security groups to use
when launching new nodes. Instead of hardcoding resource IDs, it uses **tag-based
discovery**:

```hcl
# vpc.tf
private_subnet_tags = {
  "karpenter.sh/discovery" = var.cluster_name
}
```

```yaml
# manifests/karpenter/ec2nodeclass.yaml
subnetSelectorTerms:
  - tags:
      karpenter.sh/discovery: eks-spark-lab
```

Karpenter queries EC2 for all subnets/SGs with that tag and uses them. This is why
the tag value matches the cluster name — it's a convention that scales across
multiple clusters.

---

## 4. Amazon EKS

### What is EKS?

EKS (Elastic Kubernetes Service) is the AWS managed Kubernetes control plane.
AWS runs the API server, etcd, scheduler, and controller manager for you. You only
manage the **worker nodes** (the EC2 instances where your pods run).

### Control plane vs. data plane

```
Control Plane (AWS managed, ~$0.10/hr)
  ├── kube-apiserver      -- handles kubectl commands
  ├── etcd                -- stores all cluster state
  ├── kube-scheduler      -- assigns pods to nodes
  └── kube-controller-manager -- reconciles desired vs actual state

Data Plane (you manage)
  ├── System Node Group   -- 2x t3.medium, always running
  │   ├── Karpenter controller
  │   ├── CoreDNS
  │   ├── kube-proxy
  │   └── VPC CNI
  └── Karpenter Nodes     -- created/destroyed on demand
      └── Your Spark pods
```

### Managed Node Groups vs. Karpenter nodes

This project has **two types of nodes**:

**Managed Node Group** (`role=system`): Fixed size (2 nodes, always running). This
is where Karpenter itself runs. You cannot let Karpenter manage its own nodes —
that would be a chicken-and-egg problem. If Karpenter terminated its own node,
nothing could provision new ones.

**Karpenter-managed nodes**: Dynamic. Created when pods can't fit anywhere, destroyed
when idle. This is where your workloads run.

### Access entries (replacing aws-auth ConfigMap)

EKS 1.28+ supports **access entries** to control who can authenticate to the cluster:

```hcl
# eks.tf
access_entries = {
  karpenter = {
    principal_arn = aws_iam_role.karpenter_node.arn
    type          = "EC2_LINUX"
  }
}
```

This tells EKS: "EC2 instances using the `karpenter_node` IAM role are allowed to
join the cluster as Linux worker nodes." They automatically get the
`system:bootstrappers` and `system:nodes` Kubernetes groups, which are what kubelet
needs to register with the API server.

Before access entries, this was done with the `aws-auth` ConfigMap, which was
error-prone and caused many cluster lockout incidents. Access entries are the modern
approach.

### Public endpoint

```hcl
cluster_endpoint_public_access = true
```

This allows your local machine to talk to the Kubernetes API. In production you
might set this to `false` and use a VPN or bastion, but for a learning environment
this is fine.

---

## 5. IAM & IRSA

### Why IAM matters for Kubernetes

Kubernetes pods need AWS permissions to do things like:
- Karpenter: launch and terminate EC2 instances
- Spark: read/write S3 data
- Any pod: call AWS APIs

The old way was to give the entire EC2 node an IAM role, meaning every pod on that
node got the same permissions. This is bad for security (blast radius is huge if
one pod is compromised).

### IRSA (IAM Roles for Service Accounts)

IRSA lets you assign an IAM role to a **specific Kubernetes service account**, so
only pods using that service account get those permissions.

How it works:

```
1. EKS creates an OIDC Provider (identity federation endpoint)
2. IAM role has a trust policy: "Allow tokens from this OIDC provider, 
   but only for service account karpenter in namespace kube-system"
3. Karpenter's pod uses service account 'karpenter'
4. AWS SDK in the pod calls EKS OIDC endpoint to exchange token for IAM credentials
5. Pod gets temporary AWS credentials with only the permissions in the IAM role
```

In Terraform:

```hcl
# karpenter.tf - trust policy
{
  Effect = "Allow"
  Principal = {
    Federated = module.eks.oidc_provider_arn
  }
  Action = "sts:AssumeRoleWithWebIdentity"
  Condition = {
    StringEquals = {
      "${module.eks.oidc_provider}:sub" = 
        "system:serviceaccount:kube-system:karpenter"
    }
  }
}
```

In Helm (when installing Karpenter):

```yaml
serviceAccount:
  annotations:
    eks.amazonaws.com/role-arn: <karpenter_irsa_role_arn>
```

### Karpenter's IAM permissions

The Karpenter controller IAM role has permissions to:

| Permission group | Why needed |
|---|---|
| `ec2:RunInstances`, `ec2:TerminateInstances` | Launch and kill nodes |
| `ec2:Describe*` | Find subnets, SGs, AMIs, instance types |
| `ec2:CreateLaunchTemplate` | Create EC2 launch templates for nodes |
| `pricing:GetProducts` | Get on-demand prices for instance selection |
| `ssm:GetParameter` | Fetch latest AL2023 AMI ID from AWS Parameter Store |
| `eks:DescribeCluster` | Get cluster details for bootstrapping nodes |
| `sqs:*` | Read interruption events from the SQS queue |
| `iam:PassRole` | Attach the node IAM role to new EC2 instances |

### Node IAM role

EC2 nodes themselves also need an IAM role with:

| Policy | Why |
|---|---|
| `AmazonEKSWorkerNodePolicy` | kubelet can call EKS APIs |
| `AmazonEKS_CNI_Policy` | VPC CNI can manage ENIs (network interfaces) |
| `AmazonEC2ContainerRegistryReadOnly` | Pull images from ECR |
| `AmazonSSMManagedInstanceCore` | Connect via Session Manager (no SSH needed) |

---

## 6. Karpenter

### What problem does Karpenter solve?

The traditional Kubernetes auto-scaling approach:

```
Cluster Autoscaler (old way)
  - Works with node groups
  - Scales node groups up/down
  - Limited to instance types defined in the node group
  - Slow: polls every 10 seconds, then waits for instance to be ready
  - Cannot bin-pack efficiently
```

Karpenter (new way):

```
  - No node groups needed
  - Looks at pending pods directly
  - Picks the OPTIMAL instance type for the workload
  - Launches instances directly via EC2 API
  - Consolidates underutilized nodes automatically
```

### Key Kubernetes resources

Karpenter introduces two CRDs (Custom Resource Definitions):

**EC2NodeClass** (`manifests/karpenter/ec2nodeclass.yaml`): AWS-specific configuration.
Answers "what kind of EC2 instance?"

```yaml
spec:
  amiSelectorTerms:
    - alias: al2023@latest    # Latest Amazon Linux 2023 AMI
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: eks-spark-lab
  securityGroupSelectorTerms:
    - tags:
        karpenter.sh/discovery: eks-spark-lab
  instanceProfile: KarpenterNodeInstanceProfile-eks-spark-lab
  blockDeviceMappings:
    - ebs:
        volumeSize: 50Gi
        volumeType: gp3
        deleteOnTermination: true
```

**NodePool** (`manifests/karpenter/nodepool.yaml`): Scheduling constraints and limits.
Answers "what workloads go here and within what budget?"

```yaml
spec:
  template:
    spec:
      requirements:
        - key: karpenter.k8s.aws/instance-family
          operator: In
          values: ["m5", "m5a", "r5", "r5a"]
        - key: karpenter.k8s.aws/instance-size
          operator: In
          values: ["xlarge", "2xlarge", "4xlarge"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["spot", "on-demand"]
  limits:
    cpu: "40"
    memory: 160Gi
  disruption:
    consolidationPolicy: WhenEmptyOrUnderutilized
    consolidateAfter: 30s
```

### Instance family choices

| Family | Vibe | Use case |
|---|---|---|
| `m5` / `m5a` | Balanced CPU:memory (1:4) | General purpose, Spark executors |
| `r5` / `r5a` | Memory heavy (1:8) | Memory-intensive Spark jobs, large shuffles |

The `a` variants (m5a, r5a) use AMD processors instead of Intel. Same performance,
~10% cheaper.

### Spot instances

Spot instances are spare EC2 capacity sold at up to 70% discount. The catch: AWS
can reclaim them with 2 minutes notice when they need the capacity back.

Karpenter handles this gracefully:

1. EventBridge detects spot interruption warning
2. Routes event to SQS queue
3. Karpenter reads from SQS
4. Karpenter cordons the node (no new pods) and drains it (evicts existing pods)
5. Pods are rescheduled on other nodes
6. AWS terminates the instance

This is why the SQS queue and EventBridge rules in `karpenter.tf` exist. Without
this, pods would be hard-killed with no warning.

### Consolidation

When nodes become underutilized (Spark job finishes, less work to do), Karpenter
consolidates:

```
consolidationPolicy: WhenEmptyOrUnderutilized
consolidateAfter: 30s
```

- `WhenEmptyOrUnderutilized`: terminate empty nodes AND nodes where workloads could
  fit on fewer nodes
- `consolidateAfter: 30s`: wait 30 seconds before acting (avoids churn)

### AMI discovery via SSM

Karpenter uses `al2023@latest` which is an alias. Behind the scenes it calls AWS
SSM Parameter Store to resolve the actual AMI ID:

```
/aws/service/eks/optimized-ami/1.31/amazon-linux-2023/x86_64/standard/recommended
```

This means you always get the latest patched AMI without manually updating IDs.

---

### 6.1 nginx Karpenter Validation

Once Karpenter is installed on EKS, use nginx to validate it end-to-end. This is
better than a pause-container test because it proves more things at once:

| What it proves | How |
|---|---|
| Karpenter provisions a node | pod has `nodeSelector: nodepool=default`, can't land on system nodes |
| EC2 bootstrapping works | kubelet registers, node becomes Ready |
| Image pull works | nginx image is pulled from Docker Hub |
| DNS resolution works | nginx can resolve internal names |
| The node serves real traffic | `port-forward` + `curl` returns HTTP 200 |

The key is `nodeSelector: nodepool=default`. The NodePool sets this label on every
node it launches (see `manifests/karpenter/nodepool.yaml` line 8). Pods with this
selector can never schedule on the system managed node group, so Karpenter is forced
to act.

```bash
# deploy
kubectl apply -f manifests/karpenter/validate-nginx.yaml

# watch a node appear (~60-90s)
kubectl get nodes -w

# watch pods go Pending → Running
kubectl get pods -l app=karpenter-validate -w

# verify nginx serves traffic
kubectl port-forward svc/karpenter-validate 8080:80 &
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080
# expected: 200

# clean up — then watch the node disappear after ~30s
kubectl delete -f manifests/karpenter/validate-nginx.yaml
kubectl get nodes -w
```

**Why 3 replicas?** It makes the test visible — one pod landing on a node could
be a coincidence, three pods all Running on a Karpenter node is unambiguous.

**Why nginx:stable-alpine and not nginx:latest?** `stable-alpine` is a fixed,
smaller image (~25MB vs ~185MB for the full image). Faster to pull in CI and on
a fresh node.

---

## 7. Kubeflow Spark Operator

### What is Spark Operator?

Apache Spark normally runs in YARN or Standalone mode. To run on Kubernetes, Spark
has a native Kubernetes mode, but submitting jobs requires running `spark-submit`
with complex arguments and no visibility into the job as a Kubernetes object.

The Kubeflow Spark Operator wraps all of that into a Kubernetes-native CRD:
`SparkApplication`. You declare what you want, the operator handles the rest —
and the job becomes a first-class Kubernetes resource you can `kubectl get`, watch,
and describe like anything else.

---

### 7.1 Installing the Spark Operator via Helm

Helm is a package manager for Kubernetes. The Spark Operator is distributed as a
Helm chart, which bundles all the Kubernetes manifests (Deployment, RBAC, CRDs,
webhooks) into a versioned, configurable package.

```bash
# add the Kubeflow Spark Operator chart repository
helm repo add spark-operator https://kubeflow.github.io/spark-operator
helm repo update

# install the operator into its own namespace
helm upgrade --install spark-operator spark-operator/spark-operator \
  --namespace spark-operator \
  --create-namespace \
  --set webhook.enable=true \
  --wait
```

**What each flag does:**

| Flag | Why |
|---|---|
| `upgrade --install` | Install if not present, upgrade if already installed — idempotent |
| `--namespace spark-operator` | Isolate operator pods from application workloads |
| `--create-namespace` | Create the namespace if it doesn't exist |
| `--set webhook.enable=true` | Enable the mutating admission webhook (explained below) |
| `--wait` | Block until all pods are Ready (good for scripting) |

**What gets installed:**

```
spark-operator namespace
  ├── Deployment: spark-operator          <- the operator controller
  ├── ServiceAccount: spark-operator
  ├── ClusterRole: spark-operator         <- permissions to watch SparkApplications
  ├── ClusterRoleBinding
  ├── MutatingWebhookConfiguration        <- intercepts pod creation
  └── CRDs:
      ├── sparkapplications.sparkoperator.k8s.io
      └── scheduledsparkapplications.sparkoperator.k8s.io
```

**What is a webhook?**

A `MutatingWebhookConfiguration` tells Kubernetes: "before creating any pod with
label `spark-role=executor`, send the request to my webhook server first."

The Spark Operator webhook intercepts executor pod creation requests and injects
environment variables, volume mounts, and configuration that Spark needs. Without
the webhook, you would have to set all of this manually in each SparkApplication.

```
kubectl apply SparkApplication
  |
  v
Operator creates Driver pod
  |
  v
Driver calls Kubernetes API: create Executor pod
  |
  v
Kubernetes API sends request to webhook (MutatingWebhookConfiguration)
  |
  v
Webhook adds: SPARK_EXECUTOR_ID, SPARK_DRIVER_URL, volume mounts...
  |
  v
Executor pod is created with all required configuration injected
```

Verify the operator is running:

```bash
kubectl get pods -n spark-operator
# NAME                                  READY   STATUS    RESTARTS
# spark-operator-5f7d9b8c4d-xxxx        1/1     Running   0

kubectl get crd | grep spark
# sparkapplications.sparkoperator.k8s.io
# scheduledsparkapplications.sparkoperator.k8s.io
```

---

### 7.2 RBAC for the Spark Service Account

This is one of the most misunderstood parts of running Spark on Kubernetes.

**The problem**: In cluster mode, the Driver pod calls the Kubernetes API to create
Executor pods. For that call to succeed, the pod needs a service account with
permission to manage pods. By default, pods use the `default` service account which
has no permissions.

**The solution**: Create a dedicated `spark` service account with the right RBAC.

#### What RBAC is

RBAC (Role-Based Access Control) is Kubernetes's permission system. It has three
building blocks:

```
ServiceAccount  →  bound to  →  Role (or ClusterRole)
                               |
                               └── rules: who can do what to which resources
```

- **ServiceAccount**: an identity for a pod (like a user, but for processes)
- **Role**: a set of permissions scoped to a namespace
- **ClusterRole**: same, but cluster-wide
- **RoleBinding**: glues a ServiceAccount to a Role in a namespace
- **ClusterRoleBinding**: glues a ServiceAccount to a ClusterRole cluster-wide

#### Full RBAC manifest for Spark

```yaml
# 1. The identity
apiVersion: v1
kind: ServiceAccount
metadata:
  name: spark
  namespace: default

---
# 2. The permissions (scoped to the default namespace)
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: spark-role
  namespace: default
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch", "create", "delete", "patch"]
  - apiGroups: [""]
    resources: ["services"]
    verbs: ["get", "create", "delete"]
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "create", "delete"]

---
# 3. Bind the identity to the permissions
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: spark-role-binding
  namespace: default
subjects:
  - kind: ServiceAccount
    name: spark
    namespace: default
roleRef:
  kind: Role
  name: spark-role
  apiGroup: rbac.authorization.k8s.io
```

**Why does the driver need these specific permissions?**

| Resource | Verbs | Why |
|---|---|---|
| pods | create | Driver creates executor pods |
| pods | get, list, watch | Driver monitors executor health |
| pods | delete | Driver cleans up executors on completion |
| services | create, get, delete | Driver creates a headless service for executor→driver communication |
| configmaps | create, get, delete | Driver stores Spark configuration |

**For local validation (KinD)**, the quickest approach is:

```bash
kubectl create serviceaccount spark -n default

kubectl create clusterrolebinding spark-role \
  --clusterrole=edit \
  --serviceaccount=default:spark \
  --namespace=default
```

The built-in `edit` ClusterRole covers all the permissions above. In production,
use the tighter Role manifest shown above.

#### How the service account is referenced

In `manifests/spark/example-app.yaml`:

```yaml
driver:
  serviceAccount: spark   # <-- the Driver pod runs as this identity
```

The Driver pod gets a token mounted at `/var/run/secrets/kubernetes.io/serviceaccount/token`.
When it calls `kubectl`/the Kubernetes API internally, that token is used to authenticate
and RBAC checks run against the `spark` service account's permissions.

---

### 7.3 SparkApplication CRD — Full Anatomy

A CRD (Custom Resource Definition) extends the Kubernetes API with a new resource type.
`SparkApplication` is not a built-in Kubernetes object — the Spark Operator installs it.
Once installed, `kubectl get sparkapplication` works just like `kubectl get pod`.

Full annotated spec from `manifests/spark/example-app.yaml`:

```yaml
apiVersion: sparkoperator.k8s.io/v1beta2
kind: SparkApplication
metadata:
  name: spark-pi
  namespace: default
spec:
  # --- Language and runtime ---
  type: Python              # Python (PySpark). Also: Scala, Java, R
  pythonVersion: "3"        # Python 3. Required when type: Python

  # --- Execution mode ---
  mode: cluster             # Driver runs as a Kubernetes pod
                            # "client" = driver runs on your local machine

  # --- Container image ---
  image: "apache/spark:3.5.1-python3"
  imagePullPolicy: Always   # Always pull (good for dev). Use IfNotPresent locally.

  # --- The code to run ---
  mainApplicationFile: local:///opt/spark/examples/src/main/python/pi.py
  # "local://" prefix = file inside the container image (not S3 or HDFS)
  arguments:
    - "1000"                # Passed as sys.argv to the Python script

  sparkVersion: "3.5.1"    # Must match the Spark version in the image

  # --- Restart behavior ---
  restartPolicy:
    type: Never             # Don't retry on failure. Also: Always, OnFailure

  # --- Driver pod configuration ---
  driver:
    cores: 1                # CPU request AND limit (Spark uses integer cores)
    coreLimit: "1200m"      # CPU limit slightly above request for JVM/GC spikes
    memory: "1g"            # Memory request (Spark adds overhead on top of this)
    labels:
      version: 3.5.1        # Arbitrary labels for filtering with kubectl
    serviceAccount: spark   # The service account with pod-management permissions

  # --- Executor pod configuration ---
  executor:
    cores: 1
    instances: 2            # How many executor pods to launch
    memory: "1g"
    labels:
      version: 3.5.1
```

**Memory overhead**: Spark adds a memory overhead on top of what you specify
(default: 10% or 384MB, whichever is larger). So `memory: "1g"` actually requests
about 1.4GB from Kubernetes. Keep this in mind when calculating node capacity.

**`local://` vs other schemes**:

| Prefix | Where the file lives |
|---|---|
| `local://` | Baked into the container image |
| `s3://` | Amazon S3 |
| `hdfs://` | HDFS |
| `http://` | HTTP server |

For production jobs, the code would typically be in the image (`local://`) or on S3.

---

### 7.4 Job Lifecycle — Status Transitions

When you apply a SparkApplication, it goes through a predictable set of states:

```
(not created)
     |
     | kubectl apply
     v
SUBMITTED  -- operator received it, creating driver pod
     |
     | driver pod scheduled and started
     v
RUNNING    -- driver is alive, creating executors
     |
     |-- job succeeds --> SUCCEEDING --> COMPLETED
     |
     '-- job fails   --> FAILING    --> FAILED
```

Watch the transitions in real time:

```bash
kubectl get sparkapplication -w
# NAME       STATUS      ATTEMPTS   START                  FINISH   AGE
# spark-pi   SUBMITTED   1          2024-01-01T10:00:00Z   <none>   5s
# spark-pi   RUNNING     1          2024-01-01T10:00:00Z   <none>   20s
# spark-pi   SUCCEEDING  1          2024-01-01T10:00:00Z   <none>   90s
# spark-pi   COMPLETED   1          2024-01-01T10:00:00Z   ...      95s
```

Inspect the full status object:

```bash
kubectl describe sparkapplication spark-pi
# Shows:
#   Status:
#     Application State:
#       State: COMPLETED
#     Driver Info:
#       Pod Name: spark-pi-driver
#       Pod State: Succeeded
#     Executor State:
#       spark-pi-exec-1: COMPLETED
#       spark-pi-exec-2: COMPLETED
#     Last Submission Attempt Time: ...
```

---

### 7.5 Driver vs. Executor — Deep Dive

```
SparkApplication (the declaration)
  |
  v
Driver Pod (1 pod, always)
  ├── runs the user's Python/Scala/Java code
  ├── creates and manages SparkContext
  ├── calls Kubernetes API to create Executor pods
  ├── divides the data into partitions
  ├── schedules tasks on executors
  └── collects results and writes output

Executor Pods (N pods, configurable)
  ├── receive tasks from the driver
  ├── read data (from S3, HDFS, etc.)
  ├── process partitions
  ├── cache data in memory between stages
  └── report results back to driver
```

**Task vs. Partition vs. Stage**:

- **Partition**: a chunk of the input data. If you have 100GB of data split into
  100 partitions, each partition is ~1GB.
- **Task**: one unit of work = one function applied to one partition.
- **Stage**: a group of tasks that can run in parallel without shuffling data.
- **Job**: triggered by an action (like `.collect()` or `.write()`), broken into stages.

```
your_data.map(transform).filter(keep).groupBy(key).count()
      |          |            |          |
    Stage 1: map + filter (no shuffle needed)
                               |
                           Stage 2: groupBy requires shuffle (data moves between executors)
                                         |
                                     Stage 3: count (aggregation)
```

**Shuffle**: when data needs to be redistributed between executors (e.g., for groupBy,
join, sort). This is the most expensive operation in Spark — it requires network I/O
between all executor pods.

**Resource calculation for this example**:

```
Driver:       1 core,  1GB → Kubernetes sees ~1 core, ~1.4GB (with overhead)
Executor 1:   1 core,  1GB → Kubernetes sees ~1 core, ~1.4GB
Executor 2:   1 core,  1GB → Kubernetes sees ~1 core, ~1.4GB
                                                       
Total request: ~3 cores, ~4.2GB

Karpenter picks: m5.xlarge (4 vCPU, 16GB)
  - fits all 3 pods
  - leaves headroom for DaemonSets (VPC CNI ~256MB, kube-proxy ~64MB)
```

---

### 7.6 The Kubernetes Scheduling Flow

Understanding how a pod goes from "declared" to "running" is fundamental. Every
Spark pod goes through this exact flow.

```
kubectl apply SparkApplication
        |
        v
[1] API Server validates and stores the object in etcd
        |
        v
[2] Spark Operator controller notices new SparkApplication (via watch)
        |
        v
[3] Operator calls spark-submit → creates Driver Pod object in etcd
        |
        v
[4] kube-scheduler notices Driver Pod has no node assigned (nodeName: "")
        |
        v
[5] kube-scheduler evaluates all nodes:
    - Filtering: remove nodes that can't fit the pod
      (not enough CPU/memory, taints not tolerated, node not Ready...)
    - Scoring: rank remaining nodes (prefer less loaded, better locality...)
    |
    v
[6] kube-scheduler writes nodeName into the Pod object
        |
        v
[7] kubelet on that node notices a pod assigned to it (via watch)
        |
        v
[8] kubelet pulls the container image (if not cached)
        |
        v
[9] kubelet creates the container via the container runtime (containerd)
        |
        v
[10] Pod status → Running
        |
        v
[11] Driver starts → calls Kubernetes API: create 2 Executor pods
        |
        v
[12] Steps 4–10 repeat for each Executor pod
        |
        v
[13] All pods Running → Spark job executes
        |
        v
[14] Job finishes → pods enter Completed/Succeeded state
        |
        v
[15] Operator updates SparkApplication.status → COMPLETED
```

**On EKS with Karpenter**, step 5 has a twist: if no node has enough capacity,
the pod stays Pending. Karpenter detects this (step 5b) and provisions an EC2
instance. Only after the node joins the cluster does the scheduler assign the pod.

**On KinD**, there are fixed nodes (2 workers). If the pod fits, it schedules
immediately. If it doesn't fit (e.g., too many pods), it stays Pending forever
because there's nothing to add nodes.

#### Observing the scheduling flow

```bash
# watch a pod's events (shows each scheduling decision)
kubectl describe pod spark-pi-driver
# Events:
#   Normal  Scheduled    0s    default-scheduler  Successfully assigned default/spark-pi-driver to node-1
#   Normal  Pulling      0s    kubelet            Pulling image "apache/spark:3.5.1-python3"
#   Normal  Pulled       30s   kubelet            Successfully pulled image
#   Normal  Created      30s   kubelet            Created container spark-kubernetes-driver
#   Normal  Started      30s   kubelet            Started container spark-kubernetes-driver

# watch all events in the namespace
kubectl get events --sort-by='.lastTimestamp'

# see scheduler decisions in real time
kubectl get pods -w
```

#### What "Pending" means

A pod in `Pending` state means the scheduler has not found a suitable node yet.
Common reasons:

| Reason | kubectl describe shows |
|---|---|
| Not enough CPU/memory | `Insufficient cpu` or `Insufficient memory` |
| No node matches taints/tolerations | `node(s) had untolerated taint` |
| No node matches nodeSelector | `node(s) didn't match Pod's node affinity` |
| Image pull in progress | Pod is assigned but container not started yet |
| Karpenter is provisioning | Pod is Pending, Karpenter logs show launch in progress |

---

### 7.7 cluster mode vs. client mode

**cluster mode** (`mode: cluster`): Driver runs as a Kubernetes pod.

```
your machine  →  kubectl apply  →  API Server  →  Operator  →  Driver Pod (in cluster)
                                                                     |
                                                               Executor Pods
```

- Driver is managed by Kubernetes (restarted if it crashes, depending on restartPolicy)
- You can close your terminal and the job keeps running
- Logs are in the pod, fetch with `kubectl logs spark-pi-driver`
- Good for production and scheduled jobs

**client mode**: Driver runs on your local machine.

```
your machine  →  spark-submit  →  Driver (on your machine)  →  API Server  →  Executor Pods
```

- If your machine dies or you close the terminal, the job fails
- Good for interactive development (driver output goes to your terminal)
- Executors still run in Kubernetes

---

### 7.8 Useful commands

```bash
# list all Spark jobs across all namespaces
kubectl get sparkapplication -A

# watch job status transitions in real time
kubectl get sparkapplication -w

# describe a job (full spec + status + events)
kubectl describe sparkapplication spark-pi

# get driver logs
kubectl logs spark-pi-driver

# follow driver logs while job runs
kubectl logs -f spark-pi-driver

# get executor logs
kubectl logs spark-pi-exec-1

# check which pods a SparkApplication created
kubectl get pods -l spark-app-name=spark-pi

# delete a job (also removes driver and executor pods)
kubectl delete sparkapplication spark-pi
```

---

## 8. Validation Strategy — Local vs. Production

This project is designed so you can validate incrementally — cheap and fast first,
real infrastructure only when needed.

### The two paths side by side

```
LOCAL PATH (KinD)                       PRODUCTION PATH (AWS EKS)
─────────────────────────────────────   ──────────────────────────────────────
cost: free                              cost: ~$0.10/hr control plane + EC2
speed: cluster ready in 60s             speed: cluster ready in ~15 min
AWS account: not needed                 AWS account: required

What you validate:                      What you validate (everything above plus):
  ✓ Spark Operator Helm install           ✓ Karpenter node provisioning
  ✓ SparkApplication CRD lifecycle        ✓ Spot instance interruption handling
  ✓ Driver + executor pod flow            ✓ IRSA / IAM role federation
  ✓ RBAC for service accounts             ✓ EC2 bootstrapping (kubelet → EKS)
  ✓ Kubernetes scheduling concepts        ✓ SQS interruption queue
  ✓ kubectl inspect + debug skills        ✓ Tag-based subnet/SG discovery
  ✓ Karpenter CRD structure (inspect)     ✓ NodePool limits and consolidation
  ✗ Actual node provisioning              ✓ nginx → Karpenter validation
  ✗ IRSA / real IAM                       ✓ Real Spark on real EC2 nodes
```

### When to use each

Use **KinD** when:
- You are learning Kubernetes concepts for the first time
- You want to iterate on Spark job specs without paying for EC2
- You want to run the integration test in CI (no AWS credentials needed)
- You broke something and need a clean cluster in 60 seconds

Use **EKS** when:
- You need to validate Karpenter's actual provisioning behavior
- You are testing IAM/IRSA configuration from Terraform
- You want to validate Spot interruption handling via SQS
- You are deploying to a real environment

### The validation order

```
1. KinD — Spark Operator + scheduling (zero cost)
      |
      | confident Spark side works?
      v
2. EKS — terraform apply (infrastructure)
      |
      v
3. EKS — helm install karpenter (node autoscaling)
      |
      v
4. EKS — kubectl apply validate-nginx.yaml (Karpenter provisioning)
      |
      | node appears, nginx returns 200?
      v
5. EKS — kubectl apply example-app.yaml (full Spark on real EC2)
```

### What CI validates automatically

The three GitHub Actions workflows map directly to this strategy:

| Workflow | Path | What it covers |
|---|---|---|
| `validate.yml` | both | Terraform syntax, manifest schemas — fast, no cluster needed |
| `integration.yml` | local | Full Spark lifecycle on KinD — no AWS creds |
| `upgrade.yml` | — | Weekly version check, Claude writes the PR description |

Karpenter provisioning is **not** in CI because it requires real AWS credentials
and a live EKS cluster. It is validated manually with the nginx manifest before
any production deploy.

---

## 9. How Everything Connects (End-to-End Flow)

### Full lifecycle of a Spark job

```
1. terraform apply
   └── Creates: VPC, EKS, IAM roles, SQS, EventBridge rules
   
2. helm install karpenter
   └── Deploys Karpenter controller on system nodes
   
3. kubectl apply -f manifests/karpenter/
   └── Creates EC2NodeClass and NodePool objects
   
4. helm install spark-operator
   └── Deploys operator, creates SparkApplication CRD
   
5. kubectl apply -f manifests/spark/example-app.yaml
   └── Creates SparkApplication object
   
6. Spark Operator sees new SparkApplication
   └── Creates Driver pod (Pending - no nodes)
   
7. kube-scheduler cannot place Driver pod
   └── Pod stays Pending
   
8. Karpenter watches for Pending pods
   └── Evaluates NodePool requirements
   └── Picks optimal EC2 instance type
   └── Calls ec2:RunInstances with launch template
   
9. EC2 instance boots
   └── Kubelet registers with EKS API server
   └── Node appears as Ready
   
10. kube-scheduler places Driver pod on new node
    └── Driver starts, requests 2 Executor pods
    
11. Karpenter may expand (if more capacity needed) or
    existing node fits all pods
    
12. Spark job runs (Pi calculation with 1000 iterations)
    
13. Job completes, Driver pod enters Completed state
    
14. Operator updates SparkApplication.status = Completed
    
15. Pods are terminated (restartPolicy: Never)
    
16. Node is now empty
    
17. Karpenter consolidation triggers after 30s
    └── Calls ec2:TerminateInstances
    └── Node disappears from cluster
```

### Tag-based discovery chain

Understanding how Karpenter finds the right subnets and security groups:

```
Terraform tags subnets with:
  karpenter.sh/discovery = eks-spark-lab

EC2NodeClass references:
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: eks-spark-lab

Karpenter API call at launch time:
  ec2:DescribeSubnets (filter: tag:karpenter.sh/discovery = eks-spark-lab)
  --> Gets subnet IDs dynamically

Same pattern for security groups
```

This is why the cluster name matters — it's the glue between Terraform and Karpenter.

---

## 10. CI/CD Pipeline

The project has four GitHub Actions workflows. Understanding them reinforces the
concepts from this guide because each one exercises a different layer of the stack.

### Overview

| Workflow | Trigger | Claude involved? |
|---|---|---|
| `validate.yml` | every push / PR | yes — suggests PR body on PRs |
| `integration.yml` | every PR / manual | no |
| `infra-pr-review.yml` | PR touching `terraform/`, `manifests/`, `.github/` | yes — posts review comment |
| `upgrade.yml` | every Monday / manual | yes — writes upgrade PR description |

---

### Workflow 1 — `validate.yml` (every push / PR)

```
push or PR to main
  |
  ├── job: terraform
  │     terraform fmt -check         ← fails if any .tf file is not formatted
  │     terraform init -backend=false ← downloads providers, no AWS creds needed
  │     terraform validate            ← checks HCL syntax and references
  │
  ├── job: manifests
  │     kubeconform                   ← validates standard K8s objects against
  │                                      JSON schemas for Kubernetes 1.31
  │                                      (-ignore-missing-schemas skips CRDs)
  │
  └── job: claude-pr-body  (PRs only)
        pip install anthropic
        python claude_pr_body.py      ← if upgrades.json exists, generates PR body
                                         skips silently if no upgrades pending
```

**Why `terraform init -backend=false`?** Skips backend initialization (no
S3/DynamoDB connection) but still downloads provider plugins from the Terraform
Registry. No AWS credentials are needed.

**Why kubeconform skips CRDs?** `NodePool`, `EC2NodeClass`, and `SparkApplication`
are not built-in Kubernetes types — they are CRDs installed at runtime. Standard
objects (ServiceAccount, Deployment, Service) are still validated.

**`claude_pr_body.py` safe mode**: The script now checks for `upgrades.json` before
calling the Claude API. If the file is missing or empty it exits cleanly with code 0,
so the job never fails on non-upgrade PRs.

---

### Workflow 2 — `integration.yml` (every PR + manual)

```
PR to main (or manual trigger)
  |
  └── job: spark-on-kind
        azure/setup-kubectl@v4         ← install kubectl on the runner
        azure/setup-helm@v4            ← install Helm on the runner
        helm/kind-action@v1            ← create KinD cluster (spark-lab-ci)
        kubectl cluster-info           ← verify cluster is reachable
        kubectl get nodes -o wide      ← confirm node is Ready
        helm install spark-operator    ← with webhook enabled
        kubectl rollout status         ← wait for operator deployment to be Ready
        kubectl get crds | grep spark  ← confirm CRDs are installed
        kubectl create serviceaccount spark (|| true — idempotent)
        kubectl create clusterrolebinding spark-role (|| true)
        kubectl apply example-app-kind.yaml
        poll state every 20s, max 10 min
          → COMPLETED: exit 0
          → FAILED/SUBMISSION_FAILED: kubectl describe + exit 1
        kubectl get pods -A            ← always printed (good for debugging)
        kubectl logs spark-pi-driver   ← always printed
```

Key improvements from the original version:
- Uses `helm/kind-action@v1` (official GitHub Action) instead of manually downloading the kind binary
- Waits for operator rollout with `kubectl rollout status` before submitting the job — avoids a race condition where the webhook isn't ready yet
- Verifies CRDs are installed before submitting the SparkApplication
- `|| true` on service account creation makes it idempotent (safe to re-run)
- Prints all pods always, not just on failure

---

### Workflow 3 — `infra-pr-review.yml` (PR touching infrastructure)

This workflow only triggers when a PR changes files in `terraform/`, `manifests/`,
or `.github/`. It posts a Claude-generated review as a PR comment.

```
PR opened/updated touching terraform/, manifests/, .github/
  |
  └── job: review
        actions/checkout@v4 (fetch-depth: 0  ← full git history needed for diff)
        git fetch origin main
        python infra_pr_review.py
          → git diff origin/main...HEAD       ← full diff of the branch
          → sends diff (up to 12000 chars) to Claude API
          → Claude returns markdown with three sections:
              ## What Changed
              ## Risk Analysis
              ## Things to Verify
        actions/github-script@v7
          → posts the Claude output as a comment on the PR
             prefixed with "## 🤖 AI PR Analysis"
```

**Why `fetch-depth: 0`?** By default, `actions/checkout` fetches only the latest
commit (shallow clone). A shallow clone doesn't have enough history to compute
`git diff origin/main...HEAD`. Setting `fetch-depth: 0` fetches all commits so the
diff is complete.

**Why `paths:` filter?** The review is only useful for infrastructure changes.
Running it on a doc edit would waste API calls and produce noise.

**The diff limit (12000 chars)**: The script truncates the diff to avoid hitting
Claude's context limits. Very large diffs still get reviewed — the most important
changes are usually near the top.

---

### Workflow 4 — `upgrade.yml` (every Monday + manual)

```
schedule: every Monday 09:00 UTC
  |
  └── job: check-and-upgrade
        check_upgrades.py
          → GitHub Releases API: Karpenter, Terraform EKS module, Terraform VPC module
          → compare with pinned versions in docs/03-karpenter.md, terraform/eks.tf, terraform/vpc.tf
          → write upgrades.json if any are outdated; exit 1
          |
          | (only if upgrades found)
          v
        apply_upgrades.py
          → patches version strings in the relevant files
          |
          v
        claude_pr_body.py
          → reads upgrades.json
          → calls Claude API (claude-sonnet-4-6)
          → Claude writes a PR description with a "before merging" checklist
          |
          v
        git checkout -b chore/upgrade-YYYY-MM-DD
        git commit + push
        gh pr create --body "<claude output>"
```

**major_only flag**: Terraform modules use `~> X.0` constraints (`~> 20.0` for the
EKS module). Terraform handles minor/patch automatically — the script only flags a
new major version (21.x, 22.x...). For Karpenter (exact pin like `1.3.3`), any
newer version triggers a bump.

---

### Scripts reference

| Script | Called by | What it does |
|---|---|---|
| `check_upgrades.py` | upgrade.yml | Queries GitHub Releases API, writes `upgrades.json` |
| `apply_upgrades.py` | upgrade.yml | Patches version strings in files |
| `claude_pr_body.py` | validate.yml, upgrade.yml | Reads `upgrades.json`, generates PR description via Claude API |
| `infra_pr_review.py` | infra-pr-review.yml | Diffs branch vs main, generates risk analysis via Claude API |

---

### Required secrets

| Secret | Where to set | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | Settings → Secrets → Actions | validate, infra-pr-review, upgrade |
| `GITHUB_TOKEN` | automatic (GitHub provides it) | infra-pr-review, upgrade |

---

## 11. Cost Optimization Decisions

Every architectural decision here has a cost motivation:

| Decision | Cost impact |
|---|---|
| Single NAT gateway | ~$32/month saved (vs. one per AZ) |
| Spot instances preferred | ~70% cheaper than on-demand |
| t3.medium for system nodes | Cheapest viable instance for Karpenter + addons |
| NodePool limits (40 CPU, 160Gi) | Hard ceiling on runaway spending |
| `consolidateAfter: 30s` | Fast cleanup of idle nodes |
| `deleteOnTermination: true` on EBS | No orphaned volumes |
| 2 AZs instead of 3 | Fewer subnets, simpler, cheaper for lab |
| Local Terraform backend | No S3 + DynamoDB cost |

---

## 12. Key Concepts to Memorize

### Kubernetes fundamentals you must know

| Concept | Short definition |
|---|---|
| Pod | Smallest deployable unit, one or more containers |
| Node | VM where pods run |
| Namespace | Virtual cluster within a cluster for isolation |
| Service Account | Identity for a pod (used for RBAC and IRSA) |
| CRD (Custom Resource Definition) | Extend Kubernetes API with custom types |
| Controller | Loop that watches resources and reconciles desired vs actual state |
| RBAC | Role-Based Access Control — who can do what to which resources |
| DaemonSet | Runs one pod per node (e.g., VPC CNI, kube-proxy) |

### AWS fundamentals you must know

| Concept | Short definition |
|---|---|
| IAM Role | Set of permissions that can be assumed by a service or principal |
| OIDC | Protocol for federated identity (EKS uses it for IRSA) |
| SQS | Simple Queue Service — durable message queue |
| EventBridge | AWS event bus — routes events between AWS services |
| Spot instance | Spare EC2 capacity, cheap but interruptible |
| ENI | Elastic Network Interface — virtual NIC attached to EC2 |
| SSM Parameter Store | Secure key-value store (used for AMI IDs) |
| IRSA | IAM Roles for Service Accounts — pod-level AWS permissions |

### Questions to test your understanding

1. Why does the system node group have a fixed size of 2 nodes?
2. What happens to a Spark job if a Spot instance is interrupted mid-run?
3. Why are EKS nodes in private subnets instead of public?
4. What would happen if you removed the `karpenter.sh/discovery` tag from subnets?
5. Why does Karpenter need `iam:PassRole` permission?
6. What is the difference between NodePool limits and pod resource requests?
7. Why is `mode: cluster` preferred over `mode: client` for Spark jobs?
8. How does Karpenter know which instance types are available and their prices?
9. What role does the OIDC provider play in IRSA?
10. If you increase `executor.instances` from 2 to 20, what happens to the cluster?
11. Why can the integration test run in CI without AWS credentials, but the nginx Karpenter test cannot?
12. Why does `nodeSelector: nodepool=default` force Karpenter to act instead of using the system node group?
13. The Terraform EKS module is pinned as `~> 20.0`. If version 20.5 is released, does the upgrade workflow open a PR? Why or why not?
14. What is the difference between `terraform init -backend=false` and a normal `terraform init`?

---

*Based on the actual code in this repository.*
