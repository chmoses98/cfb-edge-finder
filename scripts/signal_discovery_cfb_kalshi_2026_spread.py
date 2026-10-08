#!/usr/bin/env python3
"""Descriptive: implied Kalshi spread for 2026 CONTROL games from catalog snapshots (last pre-kickoff snapshot)."""

import gzip
import json
import subprocess
from datetime import datetime

REPO = "/home/user/cfb-edge-finder"


def sh(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True).stdout


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


rows = [
    json.loads(l)
    for l in gzip.open(f"{REPO}/data/scripting/validation/control_market_2026/control_market_rows.jsonl.gz")
]
keys = {}
for line in sh("log", "--format=%H", "origin/main", "--", "data/live/games").split():
    for f in sh("ls-tree", "--name-only", line, "data/live/games/").split():
        keys.setdefault(f.split("/")[-1][:-5], set()).add(line)
    if len(keys) > 2000:
        pass
out = []
TITLES = {}
for r in rows:
    k = ts(r["kickoff_utc"])
    tag = k.strftime("%y%b%d").upper()
    if k < ts("2026-09-18T00:00:00Z"):
        out.append({"game": f"{r['away']}@{r['home']}", "tier": r["tier"], "status": "BEFORE_CATALOG_HISTORY"})
        continue
    cand = [key for key in keys if key.startswith(tag)]
    best = None
    for key in cand:
        if key not in TITLES:
            c0 = sorted(keys[key])[0]
            g0 = json.loads(sh("show", f"{c0}:data/live/games/{key}.json") or "{}")
            TITLES[key] = (g0.get("title") or "") + json.dumps(g0.get("identity", {}))
        tl = TITLES[key].split(" at ")
        if (
            len(tl) < 2
            or r["away"].split()[0].lower() not in tl[0].lower()
            or r["home"].split()[0].lower() not in tl[1].lower()
        ):
            continue
        for c in sh("log", "--format=%H", "origin/main", "--", f"data/live/games/{key}.json").split():
            st = json.loads(sh("show", f"{c}:data/live/cfb_catalog_status.json") or "{}")
            cap = st.get("captured_at")
            if not cap or ts(cap) >= k:
                continue
            g = json.loads(sh("show", f"{c}:data/live/games/{key}.json"))
            best = (ts(cap), key, g)
            break
    if best is None:
        out.append({"game": f"{r['away']}@{r['home']}", "tier": r["tier"], "status": "NO_PREKICK_SNAPSHOT"})
        continue
    cap, key, g = best
    team, opp = r["control_team"], r["opponent"]
    pts = []
    for m in g["markets"]:
        if m.get("family") != "game_spread" or m.get("status") not in ("active", "open"):
            continue
        sub = (m.get("yes_sub_title") or "").lower()
        ya, yb = m.get("yes_ask"), m.get("yes_bid")
        if ya is None or yb is None or not (0 < yb <= ya < 1):
            continue
        mid = (ya + yb) / 2
        st = float(m["floor_strike"])
        if sub.startswith(team.split()[0].lower()):
            pts.append((st, mid))
        elif sub.startswith(opp.split()[0].lower()):
            pts.append((-st, 1 - mid))
    pts.sort()
    implied = None
    for (s1, p1), (s2, p2) in zip(pts, pts[1:], strict=False):
        if p1 >= 0.5 >= p2 and p1 != p2:
            implied = s1 + (p1 - 0.5) / (p1 - p2) * (s2 - s1)
            break
    ladder = pts
    sd = r["settlement"] if isinstance(r["settlement"], dict) else {}
    out.append(
        {
            "game": f"{r['away']}@{r['home']}",
            "tier": r["tier"],
            "control": team,
            "snapshot_min_before": round((k - cap).total_seconds() / 60),
            "ladder_points": len(ladder),
            "implied_control_margin": None if implied is None else round(implied, 1),
            "settlement": sd,
        }
    )
print(json.dumps(out, indent=1, default=str))
