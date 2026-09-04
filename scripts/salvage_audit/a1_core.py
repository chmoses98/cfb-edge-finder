import numpy as np, pandas as pd
from common import *
import os as _os
RDATA = _os.environ.get("SALVAGE_RDATA", "/home/user/cfb-rdata")  # checkout of the research-data branch
RDATA_V2ENRICH = _os.environ.get("SALVAGE_RDATA_V2ENRICH", "/home/user/cfb-rdata-v2enrich")  # research-data-v2enrich
V2SRC = _os.environ.get("SALVAGE_V2SRC", "/home/user/cfb-v2branch/src")  # claude/cfb-model-v2-research-krupoc src/

m = pd.read_parquet('master.parquet')
print('N games', len(m), 'seasons', sorted(m.season.unique()))
# ---------- A. Spread ATS vs CLOSE (consensus median), -110 ----------
print('\n=== A. V2 vs CLOSING consensus spread, -110 pricing, bet V2 side when |V2-close|>=thr ===')
rows=[]
for thr in [0,1,2,3,4,5,7,10]:
    d = m[(m.v2_margin - m.mkt_spread_margin).abs() >= thr]
    pnl,_,_ = ats_pnl(d.v2_margin.values, d.mkt_spread_margin.values, d.margin.values)
    rows.append(summarize(pnl, f'thr>={thr}'))
print(fmt(rows))
print('\n-- per season, thr>=3 --')
rows=[]
for s,d in m.groupby('season'):
    d=d[(d.v2_margin-d.mkt_spread_margin).abs()>=3]
    pnl,_,_=ats_pnl(d.v2_margin.values,d.mkt_spread_margin.values,d.margin.values); rows.append(summarize(pnl,str(s)))
print(fmt(rows))
print('\n-- direction: V2 likes HOME vs AWAY, V2 likes FAV vs DOG (thr>=3) --')
d=m[(m.v2_margin-m.mkt_spread_margin).abs()>=3].copy()
pnl,side,_=ats_pnl(d.v2_margin.values,d.mkt_spread_margin.values,d.margin.values)
d['pnl']=pnl; d['side']=side
d['fav_side']=np.where(d.mkt_spread_margin>0,1,-1)  # home fav => +1
rows=[summarize(d.pnl[d.side>0],'V2 on HOME'),summarize(d.pnl[d.side<0],'V2 on AWAY'),
      summarize(d.pnl[d.side==d.fav_side],'V2 on FAVOURITE'),summarize(d.pnl[d.side!=d.fav_side],'V2 on UNDERDOG')]
print(fmt(rows))
# ---------- B. Totals vs close ----------
print('\n=== B. V2 total vs CLOSING consensus total, -110 ===')
rows=[]
for thr in [0,1,2,3,4,5,7]:
    d=m[(m.v2_total-m.mkt_total).abs()>=thr]
    pnl,side,_=ats_pnl(d.v2_total.values,d.mkt_total.values,d.total.values); rows.append(summarize(pnl,f'thr>={thr}'))
    if thr==3:
        rows.append(summarize(pnl[side>0],'  thr3 OVER')); rows.append(summarize(pnl[side<0],'  thr3 UNDER'))
print(fmt(rows))
print('-- totals per season thr>=3')
rows=[]
for s,d in m.groupby('season'):
    d=d[(d.v2_total-d.mkt_total).abs()>=3]
    pnl,_,_=ats_pnl(d.v2_total.values,d.mkt_total.values,d.total.values); rows.append(summarize(pnl,str(s)))
print(fmt(rows))
# ---------- C. Moneyline vs devigged consensus (2021-25) ----------
print('\n=== C. Winner: V2 p_home vs de-vigged consensus moneyline (games with ML) ===')
d=m.dropna(subset=['mkt_p_home']).copy()
print('n',len(d), d.groupby('season').size().to_dict())
def ll(p,y): p=np.clip(p,1e-6,1-1e-6); return -(y*np.log(p)+(1-y)*np.log(1-p))
for name,col in [('V2 (walk-forward sd)','v2_p_home'),('V2 eff member p','v2_eff_p'),('V2 struct member p','v2_struct_p'),('0.5.0 closed',  'v050_p_home_closed'),('market devig','mkt_p_home')]:
    dd=d.dropna(subset=[col]); l=ll(dd[col].values,dd.home_won.values)
    print(f'{name:22s} n={len(dd)} logloss={l.mean():.4f} brier={((dd[col]-dd.home_won)**2).mean():.4f}')
l_v2=ll(d.v2_p_home.values,d.home_won.values); l_mk=ll(d.mkt_p_home.values,d.home_won.values)
diff=l_v2-l_mk; print('paired LL diff V2-market', diff.mean().round(4), boot_ci(diff))
for s,dd in d.groupby('season'):
    print('  ',s, 'V2', ll(dd.v2_p_home.values,dd.home_won.values).mean().round(4), 'mkt', ll(dd.mkt_p_home.values,dd.home_won.values).mean().round(4))
# calibration bins of V2 vs market
d['bin']=pd.cut(d.v2_p_home,[0,.1,.2,.3,.4,.5,.6,.7,.8,.9,.95,1.0])
print(d.groupby('bin',observed=True).agg(n=('home_won','size'),v2=('v2_p_home','mean'),mkt=('mkt_p_home','mean'),obs=('home_won','mean')).round(3).to_string())
# Kalshi-style pnl: buy side where v2 disagrees by >= x prob points at devig price (optimistic: devig price = executable)
print('\n-- Kalshi-style contract P/L on winner, executable = de-vigged consensus (OPTIMISTIC, no vig), fee 7%*P(1-P) --')
rows=[]
for thr in [0,0.02,0.04,0.06,0.08,0.10,0.15]:
    dd=d[(d.v2_p_home-d.mkt_p_home).abs()>=thr]
    pnl,price=kalshi_pnl(dd.v2_p_home.values,dd.mkt_p_home.values,dd.home_won.values)
    r=summarize(pnl/price,f'|dp|>={thr}'); r['roi_note']='per $ risked'; rows.append(r)
print(fmt(rows))
