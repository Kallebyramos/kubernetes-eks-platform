variable "cluster_name" {
  description = "EKS cluster name. Also used as a tag value for Karpenter discovery."
  type        = string
  default     = "eks-spark-lab"
}

variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "eks_version" {
  description = "Kubernetes version for the EKS cluster."
  type        = string
  default     = "1.31"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "system_node_instance_type" {
  description = "Instance type for the system managed node group (runs Karpenter + system pods)."
  type        = string
  default     = "t3.medium"
}
