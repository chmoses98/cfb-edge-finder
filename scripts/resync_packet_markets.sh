#!/usr/bin/env bash
# Keep the RUN CFB research packets' market files in step with the execution index IN THE SAME COMMIT.
#
# data/research_packets/games/*/markets*.json are DERIVED from data/live/execution (the catalog's execution index).
# Two workflows write main on their own schedules: the catalog (data/live) and the packet build
# (data/research_packets). Before this script, whichever committed second left main holding a catalog that the
# packets disagreed with -- e.g. 2026-11-18 21:00 the catalog listed 286 contracts for 26NOV21IOWAILL while its
# markets.json (built 20:34) held 140 -- and the integrity check (scripts/verify_research_packets.py, also run in CI by
# tests/test_research_packet_integration.py) correctly failed on main and on every PR.
#
# The fix is ordering, not tolerance: every commit that changes either side re-derives the market files from the
# execution index in that commit's tree, then verifies STRICTLY (counts AND fingerprints), so no commit on main can
# hold the two out of step. The re-price is offline (--no-fetch), touches only games already in the packet index,
# keeps every football file, and reproduces a full build's markets exactly (tests/test_packet_market_resync.py).
#
#   scripts/resync_packet_markets.sh            # stage the re-derived files (before the first commit)
#   scripts/resync_packet_markets.sh --amend    # after `git pull --rebase`: fold any change into our own commit
#
# Exits non-zero, staging nothing new, if the strict verification fails: the caller must not push.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 scripts/build_research_packets.py --markets-only --no-fetch
python3 scripts/verify_research_packets.py --strict
git add -A data/research_packets

if [ "${1:-}" = "--amend" ] && ! git diff --cached --quiet; then
  if git merge-base --is-ancestor HEAD FETCH_HEAD 2>/dev/null; then
    # Our commit is already upstream (nothing of ours left after the rebase): never rewrite someone else's commit.
    git commit -q -m "cfb research packets: markets resync with committed execution index"
  else
    git commit -q --amend --no-edit
  fi
fi
