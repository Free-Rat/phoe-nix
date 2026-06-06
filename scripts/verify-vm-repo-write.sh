#!/usr/bin/env bash
set -euo pipefail

repo_url="${CONFIG_REPO_URL:-git@github.com:Free-Rat/phoe-nix-config.git}"
repo_branch="${CONFIG_REPO_BRANCH:-main}"
repo_dir="${VM_REPO_WRITE_TEST_DIR:-/tmp/phoe-nix-config-write-test}"
branch="node-ssh-verify-$(date +%s)"
timestamp="$(date -u +%FT%TZ)"

rm -rf "$repo_dir"
git clone --branch "$repo_branch" "$repo_url" "$repo_dir"
cd "$repo_dir"

git checkout -b "$branch"

if [[ -f README.md ]]; then
  printf '\nVM SSH write verification %s\n' "$timestamp" >> README.md
else
  printf '# VM SSH Write Verification\n\n%s\n' "$timestamp" > README.md
fi

git add README.md
git -c user.name="phoe-nix vm" -c user.email="phoe-nix-vm@local" commit -m "test repo SSH write from VM"
git push origin "HEAD:refs/heads/$branch"
git ls-remote --heads origin "$branch"
git push origin --delete "$branch"

printf 'verified branch %s\n' "$branch"
