#!/usr/bin/env python3
"""
Apply the version bumps described in upgrades.json to the relevant files.
Must be run after check_upgrades.py has written upgrades.json.
"""
import json
import re


def apply_upgrade(upgrade):
    name = upgrade["name"]
    filepath = upgrade["file"]
    current = upgrade["current"]
    latest = upgrade["latest"]
    major_only = upgrade["major_only"]

    with open(filepath) as f:
        content = f.read()

    if major_only:
        # Update "~> X.0" to "~> Y.0" where Y is the new major version
        new_major = latest.split(".")[0]
        old_major = current.split(".")[0]
        updated = re.sub(
            rf'(version = "~> ){re.escape(old_major)}(\.[0-9]+")',
            rf'\g<1>{new_major}.0"',
            content,
        )
    else:
        # Exact version pin: replace the full version string
        updated = content.replace(
            f'--version "{current}"',
            f'--version "{latest}"',
        )

    if updated == content:
        print(f"WARN: no change made for {name} in {filepath}")
        return

    with open(filepath, "w") as f:
        f.write(updated)

    print(f"Updated {name}: {current} -> {latest} in {filepath}")


def main():
    with open("upgrades.json") as f:
        upgrades = json.load(f)

    for upgrade in upgrades:
        apply_upgrade(upgrade)


if __name__ == "__main__":
    main()
