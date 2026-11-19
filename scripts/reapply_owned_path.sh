#!/usr/bin/env bash
# Recover from a `git pull --rebase` that stopped on a conflict in a data commit.
#
#   scripts/reapply_owned_path.sh <owned-path>
#
# Each main-only data writer OWNS one path (the catalog: data/live; the packet build: data/research_packets). The
# only shared files are the packets' market files, which are derived and re-derived by scripts/resync_packet_markets.sh
# right after this. So a conflict is resolved by construction rather than by hand: abort the rebase, start from the
# fetched upstream, put back exactly OUR tree for OUR path (deletions included), and commit it with our message.
# Everything outside the owned path is upstream's.
set -euo pipefail
own="${1:?usage: reapply_owned_path.sh <owned-path>}"

git rebase --abort 2>/dev/null || true
ours=$(git rev-parse HEAD)
msg=$(git log -1 --format=%B "$ours")
git reset -q --hard FETCH_HEAD
git rm -rq --cached --ignore-unmatch -- "$own"
rm -rf -- "$own"
git checkout "$ours" -- "$own"
git add -A -- "$own"
if git diff --cached --quiet; then
  echo "reapply: upstream already holds our ${own}; nothing of ours to re-commit"
  exit 0
fi
git commit -q -m "$msg"
