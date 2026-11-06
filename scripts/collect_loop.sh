#!/usr/bin/env bash
# Collect one Vélib' snapshot every 15 minutes for LOOP_MINUTES, committing each one.
#
# Why a loop? GitHub's own cron is unreliable for frequent schedules (on a new repo
# it ran once in 12 hours), so one job stays alive and keeps the rhythm itself.
# With LOOP_MINUTES=0 it collects exactly once (used on private repos).
set -uo pipefail

LOOP_MINUTES="${LOOP_MINUTES:-330}"
OFFSET_SECONDS=120                 # collect 2 min into each slot: :02 :17 :32 :47
END=$(( $(date +%s) + LOOP_MINUTES * 60 ))
runs=0
failures=0

commit_and_push() {
  git add -A data/
  if git diff --cached --quiet; then
    return 0
  fi
  git commit -q -m "data: snapshot $(date -u +%Y-%m-%dT%H:%MZ)"
  for attempt in 1 2 3 4 5; do
    if git pull -q --rebase origin "${GITHUB_REF_NAME:-main}" && git push -q; then
      return 0
    fi
    echo "Push failed (attempt ${attempt}), retrying in 15 s..."
    sleep 15
  done
  echo "::error::Could not push the snapshot after 5 attempts"
  return 1
}

while true; do
  runs=$((runs + 1))
  if ! python -m velib.collect; then
    failures=$((failures + 1))
    echo "::warning::Collection failed (${failures} of ${runs} so far)"
  fi
  commit_and_push || failures=$((failures + 1))

  now=$(date +%s)
  next=$(( (now / 900 + 1) * 900 + OFFSET_SECONDS ))
  if (( next + 60 > END )); then
    break
  fi
  echo "Next collection at $(date -u -d "@${next}" +%H:%M:%S) UTC"
  sleep $(( next - now ))
done

echo "Finished: ${runs} collections, ${failures} failures"
# Red run only if most collections failed (a single blip should not alarm you).
if (( failures * 2 > runs )); then
  exit 1
fi
