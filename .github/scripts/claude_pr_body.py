#!/usr/bin/env python3
"""
Call the Claude API to generate a pull request description for the available upgrades.
Reads upgrades.json and prints the PR body to stdout.

Requires: ANTHROPIC_API_KEY environment variable.
"""
import json
import os
import sys
import urllib.request


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        sys.exit(1)

    with open("upgrades.json") as f:
        upgrades = json.load(f)

    changes = "\n".join(
        f"- **{u['name']}**: `{u['current']}` → `{u['latest']}`"
        for u in upgrades
    )

    prompt = f"""You are helping maintain a personal Kubernetes learning project that uses:
- AWS EKS (managed Kubernetes)
- Karpenter v1.x (node autoscaler)
- Kubeflow Spark Operator (running Spark jobs on Kubernetes)
- Terraform (infrastructure as code)

The following dependency upgrades are available:
{changes}

Write a concise GitHub pull request body for these version bumps. Include:
1. A one-sentence summary of what this PR does
2. A bullet list of what changed (you can reference the versions above)
3. A short "Before merging" checklist with 2-3 practical things to verify
   (e.g. check changelogs for breaking changes, run terraform plan, run the KinD integration test)

Keep it short and practical. Do not include a PR title. Output only the markdown body."""

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 600,
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

    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    print(result["content"][0]["text"])


if __name__ == "__main__":
    main()
