#!/usr/bin/env python3
"""
Check for newer versions of tools used in this project.
Writes upgrades.json if any upgrades are available and exits with code 1.
Exits with code 0 if everything is up to date.
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error


def github_latest(owner, repo):
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    headers = {"User-Agent": "upgrade-checker/1.0"}
    token = os.environ.get("GH_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        return data["tag_name"].lstrip("v")
    except urllib.error.HTTPError as e:
        print(f"WARN: failed to fetch {owner}/{repo}: HTTP {e.code}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"WARN: failed to fetch {owner}/{repo}: {e}", file=sys.stderr)
        return None


# Each entry: (display_name, github_owner, github_repo, file, regex, major_only)
#
# major_only=True  → only flag when a new MAJOR version is released.
#                    Used for Terraform modules constrained with "~> X.0"
#                    (Terraform handles minor/patch automatically).
# major_only=False → flag any version newer than what is pinned.
CHECKS = [
    (
        "Karpenter",
        "aws", "karpenter-provider-aws",
        "docs/03-karpenter.md",
        r'--version "([0-9]+\.[0-9]+\.[0-9]+)"',
        False,
    ),
    (
        "Terraform EKS module",
        "terraform-aws-modules", "terraform-aws-eks",
        "terraform/eks.tf",
        r'version = "~> ([0-9]+)\.',
        True,
    ),
    (
        "Terraform VPC module",
        "terraform-aws-modules", "terraform-aws-vpc",
        "terraform/vpc.tf",
        r'version = "~> ([0-9]+)\.',
        True,
    ),
]


def main():
    upgrades = []

    for name, owner, repo, filepath, pattern, major_only in CHECKS:
        latest = github_latest(owner, repo)
        if latest is None:
            continue

        try:
            with open(filepath) as f:
                content = f.read()
        except FileNotFoundError:
            print(f"WARN: file not found: {filepath}", file=sys.stderr)
            continue

        match = re.search(pattern, content)
        if not match:
            print(f"WARN: version pattern not found in {filepath}", file=sys.stderr)
            continue

        current = match.group(1)

        if major_only:
            outdated = int(latest.split(".")[0]) > int(current.split(".")[0])
        else:
            outdated = latest != current

        status = "OUTDATED" if outdated else "ok"
        print(f"{name}: current={current}  latest={latest}  [{status}]")

        if outdated:
            upgrades.append({
                "name": name,
                "current": current,
                "latest": latest,
                "file": filepath,
                "major_only": major_only,
            })

    if upgrades:
        with open("upgrades.json", "w") as f:
            json.dump(upgrades, f, indent=2)
        print(f"\n{len(upgrades)} upgrade(s) available — see upgrades.json")
        sys.exit(1)
    else:
        print("\nAll versions are up to date.")
        sys.exit(0)


if __name__ == "__main__":
    main()
