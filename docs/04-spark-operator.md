# Kubeflow Spark Operator

The Spark Operator lets you run Spark jobs as Kubernetes-native resources using the `SparkApplication` CRD instead of managing pods manually.

## How it works

```
You apply a SparkApplication manifest
  → Operator creates driver pod
  → Driver requests executor pods
  → Karpenter provisions nodes for driver + executors
  → Job runs
  → Pods deleted → Karpenter terminates nodes
```

## Install via Helm

```bash
helm repo add spark-operator https://kubeflow.github.io/spark-operator
helm repo update

helm upgrade --install spark-operator spark-operator/spark-operator \
  --namespace spark-operator \
  --create-namespace \
  --set webhook.enable=true \
  --wait
```

Verify:

```bash
kubectl get pods -n spark-operator
```

## SparkApplication CRD

The operator introduces two CRDs:

| CRD | Purpose |
|---|---|
| `SparkApplication` | One-time Spark job |
| `ScheduledSparkApplication` | Recurring job (cron-based) |

## Run the example job

The example job runs a simple Pi calculation using PySpark.

```bash
kubectl apply -f manifests/spark/example-app.yaml
```

Watch the job:

```bash
# watch the SparkApplication status
kubectl get sparkapplication -w

# watch pods come up
kubectl get pods -w

# follow driver logs
kubectl logs -f <driver-pod-name>
```

Expected output in driver logs:

```
Pi is roughly 3.14159...
```

## SparkApplication structure

```yaml
apiVersion: sparkoperator.k8s.io/v1beta2
kind: SparkApplication
metadata:
  name: my-job
spec:
  type: Python          # or Scala, Java, R
  mode: cluster
  image: ...            # Spark image with your code
  mainApplicationFile: ... 
  driver:
    cores: 1
    memory: "1g"
    serviceAccount: spark
  executor:
    cores: 1
    memory: "1g"
    instances: 2
```

Karpenter handles node provisioning automatically based on the resource requests from driver and executor pods.

## Useful commands

```bash
# list all Spark jobs
kubectl get sparkapplication -A

# describe a job (shows events and state transitions)
kubectl describe sparkapplication <name>

# get driver logs
kubectl logs <name>-driver

# delete a completed job
kubectl delete sparkapplication <name>
```
