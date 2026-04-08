module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = var.eks_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # makes the API server reachable from your machine
  cluster_endpoint_public_access = true

  # enables IRSA (IAM Roles for Service Accounts) — required for Karpenter controller
  enable_irsa = true

  # tags the cluster security group so Karpenter can discover it
  cluster_security_group_tags = {
    "karpenter.sh/discovery" = var.cluster_name
  }

  # tags the node security group so EC2NodeClass can reference it
  node_security_group_tags = {
    "karpenter.sh/discovery" = var.cluster_name
  }

  # -------------------------------------------------------------------------
  # System managed node group
  # Runs: Karpenter controller, CoreDNS, kube-proxy, VPC CNI, etc.
  # Karpenter itself must run on nodes that are NOT managed by Karpenter.
  # -------------------------------------------------------------------------
  eks_managed_node_groups = {
    system = {
      instance_types = [var.system_node_instance_type]

      min_size     = 2
      max_size     = 2
      desired_size = 2

      labels = {
        role = "system"
      }
    }
  }

  # -------------------------------------------------------------------------
  # Access entries (replaces aws-auth configmap in EKS 1.29+)
  # Grants Karpenter-launched nodes permission to join the cluster.
  # -------------------------------------------------------------------------
  access_entries = {
    karpenter_nodes = {
      principal_arn = aws_iam_role.karpenter_node.arn

      # EC2_LINUX type automatically grants system:bootstrappers + system:nodes groups
      type = "EC2_LINUX"
    }
  }
}
