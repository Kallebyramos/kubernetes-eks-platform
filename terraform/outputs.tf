output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint. Used in the Karpenter Helm install."
  value       = module.eks.cluster_endpoint
}

output "region" {
  description = "AWS region."
  value       = var.region
}

output "karpenter_irsa_role_arn" {
  description = "ARN of the Karpenter controller IAM role. Used in the Helm serviceAccount annotation."
  value       = aws_iam_role.karpenter_controller.arn
}

output "karpenter_node_role_name" {
  description = "Name of the Karpenter node IAM role. Referenced in the EC2NodeClass instanceProfile."
  value       = aws_iam_role.karpenter_node.name
}

output "karpenter_sqs_queue_name" {
  description = "Name of the SQS interruption queue. Used in the Karpenter Helm install."
  value       = aws_sqs_queue.karpenter_interruption.name
}

output "configure_kubectl" {
  description = "Run this command to configure kubectl after the cluster is up."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}
