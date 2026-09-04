"""Build the master game table + per-book line table for the salvage audit.
Sources: research-data branch (V2 OOS preds, dataset, control preds, CFBD line caches).
"""
import gzip, json, sys
import numpy as np, pandas as pd
import os as _os
RDATA = _os.environ.get("SALVAGE_RDATA", "/home/user/cfb-rdata")  # checkout of the research-data branch
RDATA_V2ENRICH = _os.environ.get("SALVAGE_RDATA_V2ENRICH", "/home/user/cfb-rdata-v2enrich")  # research-data-v2enrich
V2SRC = _os.environ.get("SALVAGE_V2SRC", "/home/user/cfb-v2branch/src")  # claude/cfb-model-v2-research-krupoc src/

sys.path.insert(0, V2SRC)
from cfb_edge_finder.research.v2.features import matchup_frame
from cfb_edge_finder.research.v2.uncertainty import fit_scale_model
from scipy.stats import norm

R = RDATA + '/data/research'
C = RDATA + '/data/research_cache/v2'
df = pd.read_parquet(f'{R}/v2/dataset_d025.parquet')
X = matchup_frame(df)
df['early_w'] = X['early_w'].values
keep = ['game_id','season','week','season_type','postseason','kickoff','home','away','home_conf','away_conf',
        'conference_game','neutral','venue','venue_id','venue_dome','venue_elev_m','kick_hour_utc',
        'home_points','away_points','margin','total','home_won','both_fbs',
        'home_pregame_elo','away_pregame_elo','h_pre_talent','a_pre_talent','h_pre_coach_change','a_pre_coach_change',
        'h_pre_coach_tenure','a_pre_coach_tenure','h_pre_ret_percentPPA','a_pre_ret_percentPPA',
        'h_pre_ret_percentPassingPPA','a_pre_ret_percentPassingPPA','h_pre_fbs_new','a_pre_fbs_new',
        'home_rest_days','away_rest_days','home_games_so_far','away_games_so_far','home_travel_km','away_travel_km',
        'home_fcs_opp','away_fcs_opp','h_games_season','a_games_season','early_w',
        'mkt_spread_margin','mkt_spread_open_margin','mkt_total','mkt_p_home','mkt_n_books','home_slug','away_slug']
m = df[keep].copy()
pm = pd.read_parquet(f'{R}/v2/preds/ens_margin_d025_eq.parquet')[['game_id','pred_margin']].rename(columns={'pred_margin':'v2_margin'})
pt = pd.read_parquet(f'{R}/v2/preds/tot_eff_ridge_d025_affine.parquet')[['game_id','pred_total']].rename(columns={'pred_total':'v2_total'})
ptr = pd.read_parquet(f'{R}/v2/preds/tot_eff_ridge_d025.parquet')[['game_id','pred_total']].rename(columns={'pred_total':'v2_total_raw'})
ps = pd.read_parquet(f'{R}/v2/preds/struct_pre_ridge_d025.parquet')[['game_id','pred_margin','p_home']].rename(columns={'pred_margin':'v2_struct_margin','p_home':'v2_struct_p'})
pe = pd.read_parquet(f'{R}/v2/preds/eff_pre_ridge_d025.parquet')[['game_id','pred_margin','p_home']].rename(columns={'pred_margin':'v2_eff_margin','p_home':'v2_eff_p'})
m = m.merge(pm,on='game_id',how='inner').merge(pt,on='game_id',how='left').merge(ptr,on='game_id',how='left').merge(ps,on='game_id',how='left').merge(pe,on='game_id',how='left')
ctrl = pd.read_csv(f'{R}/v2/control_predictions.csv')
ctrl['game_id']=ctrl.game_id.astype(str)
m = m.merge(ctrl[['game_id','ctrl_margin','ctrl_total','ctrl_p_home_closed','v050_margin','v050_p_home_closed','margin_sd','total_sd']].rename(columns={'margin_sd':'ctrl_margin_sd','total_sd':'ctrl_total_sd'}),on='game_id',how='left')
assert (m.both_fbs).all(), m.both_fbs.value_counts()
m = m.sort_values(['season','week','kickoff','game_id']).reset_index(drop=True)
# ---- walk-forward V2 uncertainty (spec: log sd = b0 + b1|pm| + b2 early_w + b3 fcs + b4 pred_total) ----
m['abs_pred_margin']=m.v2_margin.abs(); m['fcs_involved']=0.0; m['pred_total_level']=m.v2_total
m['res_m']=m.margin-m.v2_margin; m['res_t']=m.total-m.v2_total
m['v2_sd_m']=np.nan; m['v2_sd_t']=np.nan; m['sd_source']='none'
for y in sorted(m.season.unique()):
    hist=m[m.season<y]
    cur=m.season==y
    if len(hist)<300:
        # 2017 fold: no prior OOS residuals. Use a constant from the 2017 *market* residual std? No - use a fixed 16.0 and flag.
        m.loc[cur,'v2_sd_m']=16.0; m.loc[cur,'v2_sd_t']=14.5; m.loc[cur,'sd_source']='fixed_fallback'; continue
    sm=fit_scale_model(hist.res_m.values, hist, ['abs_pred_margin','early_w','fcs_involved','pred_total_level'], fit_t=False)
    st=fit_scale_model(hist.res_t.values, hist, ['pred_total_level','early_w','abs_pred_margin'], fit_t=False)
    m.loc[cur,'v2_sd_m']=sm.sd(m[cur]); m.loc[cur,'v2_sd_t']=st.sd(m[cur]); m.loc[cur,'sd_source']='walkforward'
m['v2_p_home']=1-norm.cdf((0.5-m.v2_margin)/m.v2_sd_m)   # P(margin>0) = P(margin>=1) with continuity at 0.5
# ---- per-book line table ----
rows=[]
def implied(ml): return 100/(ml+100) if ml>0 else -ml/(-ml+100)
for y in range(2014,2026):
    for kind in ('lines_regular','lines_postseason'):
        try: data=json.load(gzip.open(f'{C}/{y}/{kind}.json.gz'))
        except FileNotFoundError: continue
        for g in data:
            for l in g.get('lines') or []:
                rows.append(dict(game_id=str(g['id']),season=y,provider=l.get('provider'),
                    spread=l.get('spread'),spread_open=l.get('spreadOpen'),total=l.get('overUnder'),total_open=l.get('overUnderOpen'),
                    hml=l.get('homeMoneyline'),aml=l.get('awayMoneyline')))
L=pd.DataFrame(rows)
for c in ['spread','spread_open','total','total_open','hml','aml']: L[c]=pd.to_numeric(L[c],errors='coerce')
L['provider']=L.provider.replace({'Draft Kings':'DraftKings'})
L['mkt_margin']=-L.spread; L['mkt_margin_open']=-L.spread_open
L['p_home_raw']=L.hml.map(lambda x: implied(x) if pd.notna(x) else np.nan)
L['p_away_raw']=L.aml.map(lambda x: implied(x) if pd.notna(x) else np.nan)
L['overround']=L.p_home_raw+L.p_away_raw
L['p_home_devig']=L.p_home_raw/L.overround
L=L[L.game_id.isin(m.game_id)].reset_index(drop=True)
m.to_parquet('master.parquet'); L.to_parquet('lines.parquet')
print(m.shape, L.shape); print(m.groupby('season').size().to_dict())
print(m[['v2_margin','v2_total','v2_p_home','mkt_spread_margin','mkt_spread_open_margin','mkt_total','mkt_p_home','v050_margin']].describe().T)
print('sd by season', m.groupby('season').v2_sd_m.mean().round(2).to_dict())
print(L.groupby(['season','provider']).agg(n=('spread','count'),op=('spread_open','count'),ml=('hml','count'),tot=('total','count'),to=('total_open','count')).to_string())
