"""Re-score the cached walk-forward team predictions under the CURRENT pf_core and compare with results/summary.json.

    python -m cfb.repro_team      (needs .cache/built/team_preds.pkl + stages.pkl from a run_walkforward)

No model is refitted: every ladder prediction is the cached one; only scoring (distribution fit, CRPS, PIT, paired bootstrap) is
recomputed with the installed pf_core, so differences come from pf_core 1.1.0/1.2.0 scoring changes only.
"""
import sys, json, pickle, time, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pf_core
from cfb import paths, team_eval as te, evaluate as ev
print('pf_core', pf_core.VERSION)
TP = pickle.load(open(paths.BUILT / 'team_preds.pkl', 'rb'))
X, P = TP['X'], TP['preds']
summ = json.load(open(paths.RESULTS / 'summary.json'))
S = pickle.load(open(paths.BUILT / 'stages.pkl', 'rb'))
fams = S['team_families']
out = {}
t0 = time.time()
for fam, (y, *_r) in ev.FAMILIES.items():
    pa = P[fam]
    preds = {k: v for k, v in pa.items() if k not in ('incumbent_retired_engine', 'incumbent_script_engine_baseline', 'market_close')}
    inc = {k: v for k, v in pa.items() if k.startswith('incumbent_')}
    market = 'mkt_pred' if fam == 'team_points' else None
    kind = fams[fam]['dist_kind']
    rows, best, params, losses = te.score_family(X, fam, preds, kind, inc, market)
    old = {(r['model'], r['split']): r for r in next(f for f in summ['families'] if f['family'] == fam)['models']}
    new = {(r['model'], r['split']): r for r in rows}
    maxd = {}
    nmatch = 0
    for k, r in new.items():
        o = old.get(k)
        if not o: continue
        nmatch += 1
        for m in ('mae', 'crps', 'bias', 'cover_80', 'n_obs'):
            if r.get(m) is not None and o.get(m) is not None:
                maxd[m] = max(maxd.get(m, 0), abs(r[m] - o[m]))
    hold_new = next((r for r in rows if r['model'] == best and r['split'] == 'holdout'), None)
    hold_old = next(f for f in summ['families'] if f['family'] == fam)['holdout']
    out[fam] = {'best_new': best, 'best_old': fams[fam]['best'], 'rows_compared': nmatch, 'max_abs_diff': maxd,
                'holdout_crps': (hold_old.get('crps'), hold_new.get('crps') if hold_new else None),
                'holdout_mae': (hold_old.get('mae'), hold_new.get('mae') if hold_new else None),
                'holdout_vs_baseline': (hold_old.get('vs_baseline', {}).get('mean_diff'), (hold_new or {}).get('vs_baseline', {}).get('mean_diff') if hold_new else None)}
    print(fam, json.dumps(out[fam], default=float), flush=True)
json.dump(out, open(paths.RESULTS / 'reproduction_pf_core_1_2_0.json', 'w'), default=float, indent=1)
print('secs', time.time() - t0)
