"""
AI PR reviewer for Terraform and Kubernetes changes.
Summarizes changes and highlights potential risks.
"""

import json
import os
import subprocess
import sys
import urllib.request


def get_diff():
    return subprocess.check_output(
        ["git", "diff", "origin/main...HEAD"],
        text=True
    )


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Missing ANTHROPIC_API_KEY", file=sys.stderr)
        sys.exit(1)

    diff = get_diff()

    if not diff.strip():
        print("No changes detected.")
        sys.exit(0)

    prompt = f"""
You are reviewing a pull request in a Kubernetes platform repository.

The repo contains:
- Terraform for AWS EKS
- Kubernetes manifests
- Karpenter autoscaling configuration
- Spark Operator workloads

Review the following git diff and produce a concise PR review.

Output markdown with these sections:

## What Changed
Explain the changes in simple terms.

## Risk Analysis
Highlight potential infrastructure risks or breaking changes.

## Things to Verify
List 2-4 practical checks reviewers should perform.

Git diff:
{diff[:12000]}
"""

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 700,
        "messages": [{"role": "user", "content": prompt}],
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    print(result["content"][0]["text"])


if __name__ == "__main__":
    main()