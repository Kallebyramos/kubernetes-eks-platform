module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.cluster_name}-vpc"
  cidr = var.vpc_cidr

  # spread across 2 AZs — enough for a lab, keeps NAT cost to 1 GW
  azs             = slice(data.aws_availability_zones.available.names, 0, 2)
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = true # one NAT GW keeps costs low for a lab
  enable_dns_hostnames = true
  enable_dns_support   = true

  # tags required for the AWS Load Balancer Controller (if added later)
  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1

    # Karpenter uses this tag to discover which subnets to launch nodes into.
    # The value must match the cluster name exactly.
    "karpenter.sh/discovery" = var.cluster_name
  }
}
