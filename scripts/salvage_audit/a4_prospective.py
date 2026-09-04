import json, numpy as np, pandas as pd, collections, math
from common import *
import os as _os
RDATA = _os.environ.get("SALVAGE_RDATA", "/home/user/cfb-rdata")  # checkout of the research-data branch
RDATA_V2ENRICH = _os.environ.get("SALVAGE_RDATA_V2ENRICH", "/home/user/cfb-rdata-v2enrich")  # research-data-v2enrich
V2SRC = _os.environ.get("SALVAGE_V2SRC", "/home/user/cfb-v2branch/src")  # claude/cfb-model-v2-research-krupoc src/

R=RDATA + '/data/research'
obs=[json.loads(l) for l in open(f'{R}/observations/2026.jsonl')]
rows=[]
for x in obs:
    o=x['observation']
    rows.append(dict(key=x['observation_key'],game_id=o['game_id'],family=o.get('family'),ticker=o.get('kalshi_market_ticker'),team=o.get('team'),side=o.get('side'),
        threshold=o.get('threshold'),p_model=o.get('model_probability'),yes=o.get('executable_yes_price'),no=o.get('executable_no_price'),mid=o.get('market_midpoint'),
        label=o['snapshot_timing']['label'],hrs=o['snapshot_timing']['hours_before_kickoff'],captured_at=o['captured_at'],model_version=x['data_versions']['model_version'],
        pricing=o.get('pricing_status'),kickoff=x.get('kickoff_utc_at_capture'),run_id=x.get('run_id')))
O=pd.DataFrame(rows); O['captured_at']=pd.to_datetime(O.captured_at,utc=True); O['kickoff']=pd.to_datetime(O.kickoff,utc=True)
print('observations',len(O),'games',O.game_id.nunique(),'tickers',O.ticker.nunique())
print(O.groupby(['label']).size().to_dict()); print(O.groupby('model_version').size().to_dict()); print(O.groupby('family',dropna=False).size().to_dict())
print('YES+NO ask sum (priced):', (O.yes+O.no).describe().round(3).to_dict())
att=[json.loads(l) for l in open(f'{R}/attributions/2026.jsonl')]
A=pd.DataFrame([dict(key=a['observation_key'],state=a['state'],family=a['family'],label=a['timing_label'],game_id=a['game_id'],model_version=a['model_version'],
    entry_yes=a['entry_yes_price'],entry_no=a['entry_no_price'],p_model=a['entry_model_probability'],event_true=a['event_true'],
    closing_captured=a['closing']['closing_captured'],closing_yes=a['closing']['closing_yes_price'],closing_no=a['closing']['closing_no_price'],closing_status=a['closing']['closing_status'],
    yes_pnl=(a['yes_economics'] or {}).get('fee_adjusted_research_unit_pnl'),no_pnl=(a['no_economics'] or {}).get('fee_adjusted_research_unit_pnl'),
    hrs=a['hours_before_kickoff'],result_source=a['result_source'],final_margin=a['final_home_margin'],final_total=a['final_total_points']) for a in att])
S=A[A.state.str.startswith('SETTLED')].copy()
print('\nattribution states',A.state.value_counts().to_dict())
print('settled contracts',len(S),'games',S.game_id.nunique(),'by label',S.label.value_counts().to_dict(),'by family',S.family.value_counts().to_dict(),'by model',S.model_version.value_counts().to_dict())
print('closing captured among settled:',S.closing_captured.mean().round(3), S.closing_status.value_counts().to_dict())
# attribution coverage vs observations that should be settled: games with kickoff passed
past=O[O.kickoff<pd.Timestamp('2026-09-04',tz='UTC')]
print('observations on games kicked off before 2026-09-04:',len(past),'games',past.game_id.nunique(),'; attributed rows for those keys:',A.key.isin(past.key).sum(), '; settled:',S.key.isin(past.key).sum())
missing=past[~past.key.isin(A.key)]; print('  unattributed by label',missing.label.value_counts().to_dict(),'by game',missing.game_id.value_counts().head(8).to_dict())
# ---- model-favoured trade P/L on settled contracts ----
S['gap_yes']=S.p_model-S.entry_yes; S['gap_no']=(1-S.p_model)-S.entry_no
S['trade']=np.where(S.gap_yes>0,'yes',np.where(S.gap_no>0,'no','none'))
S['gap']=np.where(S.trade=='yes',S.gap_yes,np.where(S.trade=='no',S.gap_no,np.nan))
S['pnl']=np.where(S.trade=='yes',S.yes_pnl,np.where(S.trade=='no',S.no_pnl,np.nan))
S['price']=np.where(S.trade=='yes',S.entry_yes,np.where(S.trade=='no',S.entry_no,np.nan))
S['roi']=S.pnl/S.price
def cluster_boot(df, n=3000):
    g=df.groupby('game_id').agg(p=('pnl','sum'),c=('price','sum')); vals=[]
    for _ in range(n):
        idx=rng.integers(0,len(g),len(g)); vals.append(g.p.values[idx].sum()/g.c.values[idx].sum())
    return np.quantile(vals,[.025,.975]).round(4)
T=S[S.trade!='none']
print('\n=== Model-favoured side, one contract per settled observation (fee-adjusted, Kalshi ask), 0.4.0+0.5.0 pooled ===')
for name,g in [('ALL',T)]+[(f'label={l}',g) for l,g in T.groupby('label')]+[(f'family={f}',g) for f,g in T.groupby('family')]+[(f'model={m}',g) for m,g in T.groupby('model_version')]:
    if len(g)<5: continue
    print(f'{name:45s} n={len(g):4d} games={g.game_id.nunique():3d} hit={((g.pnl>0).mean()):.3f} ROI/$={g.pnl.sum()/g.price.sum():+.4f} cluster CI={cluster_boot(g)}')
print('-- by gap bucket (model - ask), all labels --')
T['gb']=pd.cut(T.gap,[0,.02,.05,.08,.12,.2,1.0])
for b,g in T.groupby('gb',observed=True):
    print(f'  gap {str(b):14s} n={len(g):4d} games={g.game_id.nunique():3d} hit={(g.pnl>0).mean():.3f} ROI/$={g.pnl.sum()/g.price.sum():+.4f} CI={cluster_boot(g)}')
# calibration of Kalshi vs model on settled contracts (YES side), dedupe to one label per contract (T_24H preferred)
def ll(p,y): p=np.clip(p,1e-4,1-1e-4); return -(y*np.log(p)+(1-y)*np.log(1-p))
for lab in ['EARLY_OPEN','T_7D','T_24H','T_6H','CLOSING']:
    g=S[(S.label==lab)&S.p_model.notna()]
    if len(g)<20: continue
    y=g.event_true.astype(float).values; pm=g.p_model.values; pk=g.entry_yes.values/(g.entry_yes.values+ (1-g.entry_no.values))  # normalise ask-implied yes vs (1-no ask)
    pk2=(g.entry_yes.values+(1-g.entry_no.values))/2
    print(f'{lab:10s} n={len(g)} games={g.game_id.nunique()} LL model={ll(pm,y).mean():.4f} kalshi(mid of asks)={ll(pk2,y).mean():.4f} | Brier model={((pm-y)**2).mean():.4f} kalshi={((pk2-y)**2).mean():.4f}')
# CLV: signed move of the mid toward model side, entry -> closing
C=S[S.closing_captured & (S.trade!='none')].copy()
C['entry_mid']=(C.entry_yes+(1-C.entry_no))/2; C['close_mid']=(C.closing_yes+(1-C.closing_no))/2
C['clv']=np.where(C.trade=='yes',C.close_mid-C.entry_mid,C.entry_mid-C.close_mid)
print('\n=== CLV (closing mid minus entry mid, signed toward model side) on settled contracts with CLOSING ===')
for lab,g in C.groupby('label'):
    if len(g)<5: continue
    print(f'  {lab:10s} n={len(g):4d} games={g.game_id.nunique():3d} mean CLV={g.clv.mean():+.4f} share>0={(g.clv>0).mean():.3f} share<0={(g.clv<0).mean():.3f}')
# ---- V2 artifact retro-scoring on settled Week 0/1 contracts ----
print('\n=== V2 (frozen artifact, preseason state) retro-scored on settled 2026 contracts ===')
art=json.load(open(f'{R}/v2_shadow/2026.artifact.json')); G={g['game_id']:g for g in art['games']}
sett=[json.loads(l) for l in open(f'{R}/settlements/2026.jsonl')]
slug2cfbd={s['game_id']:str(s['game_result']['source_game_id']) for s in sett if s.get('game_result') and s['game_result'].get('source_game_id')}
print('slug->cfbd map size',len(slug2cfbd),'; settled games with V2 artifact entry:',sum(1 for gid in S.game_id.unique() if G.get(slug2cfbd.get(gid)) is not None),'of',S.game_id.nunique())
def ncdf(x): return 0.5*math.erfc(-x/math.sqrt(2))
def pgt(point,sd,t): 
    cut=t+0.5 if abs(t-round(t))<1e-9 else t; return 1-ncdf((cut-point)/sd)
def plt_(point,sd,t):
    cut=t-0.5 if abs(t-round(t))<1e-9 else t; return ncdf((cut-point)/sd)
S2=S.merge(O[['key','team','side','threshold']],on='key',how='left')
v2p=[]
for r in S2.itertuples():
    g=G.get(slug2cfbd.get(r.game_id)); 
    if g is None or pd.isna(r.threshold) and r.family!='moneyline': v2p.append(np.nan); continue
    if r.family=='total':
        v2p.append(pgt(g['pred_total'],g['sd_total'],float(r.threshold)) if r.side=='over' else plt_(g['pred_total'],g['sd_total'],float(r.threshold)))
    elif r.family=='spread':
        # contract: named team wins by more than threshold. home margin convention.
        if r.team=='home': v2p.append(pgt(g['pred_margin'],g['sd_margin'],float(r.threshold)))
        else: v2p.append(pgt(-g['pred_margin'],g['sd_margin'],float(r.threshold)))
    elif r.family=='moneyline':
        ph=pgt(g['pred_margin'],g['sd_margin'],0.0); v2p.append(ph if r.team=='home' else 1-ph)
    else: v2p.append(np.nan)
S2['p_v2']=v2p
V=S2.dropna(subset=['p_v2']).copy()
print('settled contracts with V2 price',len(V),'games',V.game_id.nunique())
# sanity: compare orientation using control: correlation of p_v2 with p_model
print('corr(p_v2, control p)=%.3f  mean|diff|=%.3f' % (np.corrcoef(V.p_v2,V.p_model)[0,1], (V.p_v2-V.p_model).abs().mean()))
for lab in ['EARLY_OPEN','T_7D','T_3D','T_24H','T_6H','CLOSING']:
    g=V[V.label==lab]
    if len(g)<20: continue
    y=g.event_true.astype(float).values; pk2=(g.entry_yes.values+(1-g.entry_no.values))/2
    print(f'{lab:10s} n={len(g)} games={g.game_id.nunique()} LL V2={ll(g.p_v2.values,y).mean():.4f} control={ll(g.p_model.values,y).mean():.4f} kalshi={ll(pk2,y).mean():.4f} | Brier V2={((g.p_v2-y)**2).mean():.4f} control={((g.p_model-y)**2).mean():.4f} kalshi={((pk2-y)**2).mean():.4f}')
# V2-favoured trades at ask with fee
V['gy']=V.p_v2-V.entry_yes; V['gn']=(1-V.p_v2)-V.entry_no
V['trade']=np.where(V.gy>0,'yes',np.where(V.gn>0,'no','none')); V['gap']=np.where(V.trade=='yes',V.gy,np.where(V.trade=='no',V.gn,np.nan))
V['pnl']=np.where(V.trade=='yes',V.yes_pnl,np.where(V.trade=='no',V.no_pnl,np.nan)); V['price']=np.where(V.trade=='yes',V.entry_yes,np.where(V.trade=='no',V.entry_no,np.nan))
VT=V[V.trade!='none']
print('-- V2-favoured side P/L (fee-adjusted) --')
for name,g in [('ALL',VT)]+[(f'label={l}',g) for l,g in VT.groupby('label')]+[(f'family={f}',g) for f,g in VT.groupby('family')]:
    if len(g)<5: continue
    print(f'{name:20s} n={len(g):4d} games={g.game_id.nunique():3d} hit={(g.pnl>0).mean():.3f} ROI/$={g.pnl.sum()/g.price.sum():+.4f} CI={cluster_boot(g)}')
VT['gb']=pd.cut(VT.gap,[0,.02,.05,.08,.12,.2,1.0])
for b,g in VT.groupby('gb',observed=True):
    print(f'  V2 gap {str(b):14s} n={len(g):4d} games={g.game_id.nunique():3d} hit={(g.pnl>0).mean():.3f} ROI/$={g.pnl.sum()/g.price.sum():+.4f} CI={cluster_boot(g)}')
# game-level: V2 margin/total vs Kalshi-implied line vs actual for settled games (use spread ladders to back out kalshi line ~ 50c strike)
gl=[]
for gid,g in V[V.label=='CLOSING'].groupby('game_id'):
    a=G[slug2cfbd[gid]]; fm=g.final_margin.iloc[0]; ft=g.final_total.iloc[0]
    gl.append(dict(game_id=gid,v2_margin=a['pred_margin'],v2_total=a['pred_total'],actual_margin=fm,actual_total=ft))
gl=pd.DataFrame(gl); print('\nsettled games w/ CLOSING & V2:',len(gl),' V2 margin MAE=%.2f total MAE=%.2f' % ((gl.actual_margin-gl.v2_margin).abs().mean(),(gl.actual_total-gl.v2_total).abs().mean()))
S.to_parquet('settled_2026.parquet'); V.to_parquet('settled_2026_v2.parquet'); O.to_parquet('obs_2026.parquet')
