# VPC and EKS

This step provisions the VPC and the EKS cluster using Terraform.

## What gets created

- VPC with public and private subnets across 2 AZs
- Internet Gateway + NAT Gateway (1 NAT to keep costs low)
- EKS cluster (control plane managed by AWS)
- Managed node group with 2x `t3.medium` nodes — runs system pods only (Karpenter, CoreDNS, etc.)

> The managed node group is intentionally small. All workload nodes (Spark) are provisioned by Karpenter dynamically.

## Subnet tagging (required for Karpenter)

Karpenter discovers subnets and security groups via tags. The Terraform code applies these automatically:

| Resource | Tag | Value |
|---|---|---|
| Private subnets | `karpenter.sh/discovery` | `<cluster-name>` |
| Security group | `karpenter.sh/discovery` | `<cluster-name>` |

## Apply

```bash
cd terraform/

# initialize providers and modules
terraform init

# preview changes
terraform plan

# apply
terraform apply
```

Provisioning takes ~15 minutes (EKS control plane is the slow part).

## Update kubeconfig

```bash
aws eks update-kubeconfig \
  --region $(terraform output -raw region) \
  --name $(terraform output -raw cluster_name)

kubectl get nodes
```

You should see 2 nodes in `Ready` state (the managed node group).

## Key outputs

After `apply`, Terraform outputs values needed in the next steps:

```bash
terraform output
```

| Output | Used by |
|---|---|
| `cluster_name` | Karpenter Helm values |
| `cluster_endpoint` | Karpenter Helm values |
| `karpenter_node_role_name` | EC2NodeClass |
| `karpenter_irsa_role_arn` | Karpenter Helm values |
| `karpenter_sqs_queue_name` | Karpenter Helm values |
