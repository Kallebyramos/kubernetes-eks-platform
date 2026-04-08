# Prerequisites

Install and configure all tools before running Terraform.

## 1. AWS CLI

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version
```

Configure with your credentials:

```bash
aws configure
```

You'll be prompted for:
- `AWS Access Key ID`
- `AWS Secret Access Key`
- `Default region name` (e.g. `us-east-1`)
- `Default output format` (use `json`)

Verify access:

```bash
aws sts get-caller-identity
```

## 2. Terraform

```bash
sudo apt-get update && sudo apt-get install -y gnupg software-properties-common

wget -O- https://apt.releases.hashicorp.com/gpg | \
  gpg --dearmor | \
  sudo tee /usr/share/keyrings/hashicorp-archive-keyring.gpg > /dev/null

echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] \
  https://apt.releases.hashicorp.com $(lsb_release -cs) main" | \
  sudo tee /etc/apt/sources.list.d/hashicorp.list

sudo apt-get update && sudo apt-get install terraform
terraform --version
```

## 3. kubectl

```bash
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
kubectl version --client
```

## 4. Helm

```bash
curl https://baltocdn.com/helm/signing.asc | gpg --dearmor | sudo tee /usr/share/keyrings/helm.gpg > /dev/null
sudo apt-get install apt-transport-https --yes

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] \
  https://baltocdn.com/helm/stable/debian/ all main" | \
  sudo tee /etc/apt/sources.list.d/helm-stable-debian.list

sudo apt-get update && sudo apt-get install helm
helm version
```

## 5. Update kubeconfig after cluster creation

Once the EKS cluster is up (after the Terraform step), run:

```bash
aws eks update-kubeconfig --region <region> --name <cluster-name>
kubectl get nodes
```

## Checklist

- [ ] `aws sts get-caller-identity` returns your account ID
- [ ] `terraform --version` shows 1.x
- [ ] `kubectl version --client` works
- [ ] `helm version` works
