#!/usr/bin/env bash
# Publish the staged files of an append-only archive worktree to its isolated branch without ever losing them.
#   scripts/archive_publish.sh WORKDIR HEAD:BRANCH "commit message"
# On a rejected push it does NOT rebase (a conflict on state/latest.json used to leave a detached HEAD and a no-op "successful" push).
# It re-creates its own commit on top of the remote tip: our new files are unique per run, and our state file simply wins.
set -euo pipefail
WORKDIR="$1"; REFSPEC="$2"; MSG="$3"
BRANCH="${REFSPEC#HEAD:}"
[ "$REFSPEC" = "HEAD:${BRANCH}" ] && [ -n "$BRANCH" ] || { echo "refspec must look like HEAD:<branch>" >&2; exit 2; }
cd "$WORKDIR"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
if git diff --cached --quiet; then echo "nothing new"; exit 0; fi
git commit -q -m "$MSG"
git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://x-access-token:${GITHUB_TOKEN_FOR_ARCHIVE:?}@github.com/${GITHUB_REPOSITORY:?}.git"
for attempt in 1 2 3 4 5; do
  if git push origin "HEAD:${BRANCH}"; then echo "published on attempt ${attempt}"; exit 0; fi
  echo "push rejected (attempt ${attempt}); re-applying our files on the remote tip"
  STASH="$(mktemp -d)"
  git diff-tree --root --no-commit-id --name-only -r --diff-filter=AM HEAD | while IFS= read -r f; do
    mkdir -p "$STASH/$(dirname "$f")"; cp "$f" "$STASH/$f"
  done
  git fetch origin "${BRANCH}" --depth=1
  git reset -q --hard FETCH_HEAD
  cp -R "$STASH/." .
  git add -A
  git diff --cached --quiet && { echo "remote already contains everything"; exit 0; }
  git commit -q -m "$MSG"
  sleep $((attempt * 3))
done
echo "FAILED to publish after 5 attempts: data NOT lost locally but NOT published" >&2
exit 1
