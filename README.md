# EKS + Karpenter + Spark Operator

Personal learning stack replicating a production-grade data platform on AWS EKS.

## Stack

| Layer | Tool | Version |
|---|---|---|
| Provisioning | Terraform | ~> 1.x |
| Cluster | AWS EKS | 1.31 |
| Node Autoscaling | Karpenter | v1.x |
| Spark | Kubeflow Spark Operator | latest |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  AWS Account                                        │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  VPC                                         │   │
│  │                                              │   │
│  │  ┌─────────────┐   ┌──────────────────────┐ │   │
│  │  │ Public      │   │ Private Subnets      │ │   │
│  │  │ Subnets     │   │                      │ │   │
│  │  │ (NAT GW)    │   │  ┌────────────────┐  │ │   │
│  │  └─────────────┘   │  │ EKS Control    │  │ │   │
│  │                    │  │ Plane          │  │ │   │
│  │                    │  └────────────────┘  │ │   │
│  │                    │                      │ │   │
│  │                    │  ┌────────────────┐  │ │   │
│  │                    │  │ Managed Node   │  │ │   │
│  │                    │  │ Group          │  │ │   │
│  │                    │  │ (system pods)  │  │ │   │
│  │                    │  └────────────────┘  │ │   │
│  │                    │                      │ │   │
│  │                    │  ┌────────────────┐  │ │   │
│  │                    │  │ Karpenter      │  │ │   │
│  │                    │  │ Nodes          │  │ │   │
│  │                    │  │ (on demand)    │  │ │   │
│  │                    │  └────────────────┘  │ │   │
│  │                    └──────────────────────┘ │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

**System node group** runs: Karpenter, CoreDNS, kube-proxy, VPC CNI.  
**Karpenter nodes** run: Spark driver and executor pods (provisioned on demand, terminated when idle).

## Project Structure

```
.
├── docs/
│   ├── 01-prerequisites.md     # Tool installation (AWS CLI, Terraform, kubectl, Helm)
│   ├── 02-vpc-and-eks.md       # VPC + EKS cluster via Terraform
│   ├── 03-karpenter.md         # Karpenter install + NodePool + EC2NodeClass
│   ├── 04-spark-operator.md    # Kubeflow Spark Operator + example job
│   ├── 05-kind-validation.md   # Local validation with KinD (no AWS required)
│   └── study-guide.md          # Deep-dive study guide covering every layer
├── kind/
│   └── cluster.yaml            # KinD cluster config (1 control-plane + 2 workers)
├── terraform/
│   ├── main.tf                 # Provider config and backend
│   ├── variables.tf
│   ├── outputs.tf
│   ├── vpc.tf
│   ├── eks.tf
│   └── karpenter.tf
└── manifests/
    ├── karpenter/
    │   ├── nodepool.yaml
    │   └── ec2nodeclass.yaml
    └── spark/
        ├── example-app.yaml        # Spark Pi job for EKS
        └── example-app-kind.yaml   # Spark Pi job for local KinD (reduced resources)
```

## Guides (follow in order)

### AWS path (real cluster)

1. [Prerequisites](docs/01-prerequisites.md)
2. [VPC and EKS](docs/02-vpc-and-eks.md)
3. [Karpenter](docs/03-karpenter.md)
4. [Spark Operator](docs/04-spark-operator.md)

### Local path (no AWS, no cost)

1. [KinD Validation](docs/05-kind-validation.md) — run Spark Operator end-to-end on your machine

## CI/CD

| Workflow | Trigger | What it does |
|---|---|---|
| [validate](.github/workflows/validate.yml) | every push / PR | `terraform fmt`, `terraform validate`, manifest schema check |
| [integration](.github/workflows/integration.yml) | every PR / manual | spins up KinD, runs Spark Pi job end-to-end, asserts COMPLETED |
| [upgrade](.github/workflows/upgrade.yml) | every Monday / manual | checks latest versions of Karpenter + Terraform modules, opens a PR with bumps (PR description written by Claude) |

### Required GitHub secrets

| Secret | Used by | How to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | upgrade workflow | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| `GITHUB_TOKEN` | upgrade workflow | automatically provided by GitHub Actions — no setup needed |

## Study

For a deep-dive into every concept used in this stack (Terraform, VPC, EKS, IRSA,
Karpenter, Spark Operator, RBAC, Kubernetes scheduling flow):

- [Study Guide](docs/study-guide.md)
