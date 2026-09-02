#!/bin/bash
# Archive the V2 research artifacts (final dataset parquet + meta, experiment
# registry, per-candidate predictions for the finalists, evaluation JSONs,
# control reproduction) to the research-data orphan branch under
# data/research/v2/. Never touches main or the code branch.
#
#   scripts/v2_archive_research_artifacts.sh <work_dir> <finalist1> [<finalist2> ...]
set -euo pipefail
WORK=${1:?work dir}; shift
FINALISTS=("$@")
REPO=$(cd "$(dirname "$0")/.." && pwd)
cd "$REPO"
git fetch --depth 1 origin research-data
rm -rf /tmp/rd_v2 && git worktree add /tmp/rd_v2 FETCH_HEAD --detach
DEST=/tmp/rd_v2/data/research/v2
mkdir -p "$DEST/preds" "$DEST/eval"
cp "$WORK"/dataset*.parquet.meta.json "$DEST/" 2>/dev/null || true
cp "$WORK"/dataset.parquet "$DEST/dataset.parquet"
for f in "$WORK"/dataset_*.parquet; do
  base=$(basename "$f")
  case "$base" in *_d025*|*_d020*|*_d035*) cp "$f" "$DEST/$base";; esac
done
cp "$WORK"/tournament/registry.jsonl "$DEST/registry.jsonl"
cp "$WORK"/tournament/leaderboard_round*.csv "$DEST/" 2>/dev/null || true
cp "$WORK"/control_predictions.csv "$DEST/"
cp "$WORK"/*.json "$DEST/eval/" 2>/dev/null || true
for m in "${FINALISTS[@]}"; do cp "$WORK/tournament/preds/$m.parquet" "$DEST/preds/"; done
cd /tmp/rd_v2
git config user.name "chmoses98"
git config user.email "chmoses98@gmail.com"
git add -f data/research/v2
git commit -q -m "V2 research artifacts: dataset, registry, finalist predictions, evaluation JSON

Produced by the CFB MODEL V2 research mission (branch
claude/cfb-model-v2-research-krupoc). No 2026 outcome was used for
fitting or selection; 2026 rows carry NaN targets by construction.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_011zDnkBS9G8ZEzJR1xUsQU7"
git push origin HEAD:research-data
cd "$REPO" && git worktree remove --force /tmp/rd_v2
echo "archived to research-data:data/research/v2"
