import numpy as np, pandas as pd
from common import *
import os as _os
RDATA = _os.environ.get("SALVAGE_RDATA", "/home/user/cfb-rdata")  # checkout of the research-data branch
RDATA_V2ENRICH = _os.environ.get("SALVAGE_RDATA_V2ENRICH", "/home/user/cfb-rdata-v2enrich")  # research-data-v2enrich
V2SRC = _os.environ.get("SALVAGE_V2SRC", "/home/user/cfb-v2branch/src")  # claude/cfb-model-v2-research-krupoc src/

m=pd.read_parquet('master.parquet'); L=pd.read_parquet('lines.parquet')
m['month']=pd.to_datetime(m.kickoff).dt.month; m['tdis']=m.v2_total-m.mkt_total
d=m[m.month==9].copy()
print('=== Sep totals: dependence on market total level and direction ===')
d['lvl']=pd.cut(d.mkt_total,[0,45,52,58,65,100]); dd=d[d.tdis.abs()>=3].copy()
pnl,side,_=ats_pnl(dd.v2_total.values,dd.mkt_total.values,dd.total.values); dd['pnl']=pnl; dd['side']=side
print(dd.groupby('lvl',observed=True).apply(lambda g: pd.Series(dict(n=len(g),wp=(g.pnl>0).sum()/((g.pnl!=0).sum()),roi=g.pnl.mean(),over_share=(g.side>0).mean()))).round(3).to_string())
print('\n=== Walk-forward regression of market total residual (actual - close) on (V2 - close), Sep vs Oct+ ===')
for nm,mask in [('Sep',m.month==9),('Oct+',m.month>=10),('All',m.month>0)]:
    g=m[mask]; rows=[]
    for yr in sorted(g.season.unique()):
        tr=g[g.season<yr]; te=g[g.season==yr]
        if len(tr)<300: continue
        b=np.polyfit(tr.tdis.values,(tr.total-tr.mkt_total).values,1)[0]
        bt=np.polyfit(te.tdis.values,(te.total-te.mkt_total).values,1)[0]
        rows.append((yr,round(b,3),round(bt,3)))
    print(nm, 'slope fit on prior seasons / realised slope in test season:', rows)
    x=g.tdis.values; yv=(g.total-g.mkt_total).values; print(f'   pooled slope={np.polyfit(x,yv,1)[0]:.3f} corr={np.corrcoef(x,yv)[0,1]:.3f} n={len(g)}')
print('\n=== Sep thr>=3 by season x book (real books only) ===')
LB=L.dropna(subset=['total']).rename(columns={'total':'book_total'}).merge(m[['game_id','season','v2_total','total','month']],on=['game_id','season']); LB=LB[LB.month==9]
LB=LB[LB.provider.isin(['Bovada','DraftKings','ESPN Bet','William Hill (New Jersey)','Caesars'])]
rows=[]
for (s,prov),g in LB.groupby(['season','provider']):
    g=g[(g.v2_total-g.book_total).abs()>=3]
    if len(g)<30: continue
    pnl,_,_=ats_pnl(g.v2_total.values,g.book_total.values,g.total.values); r=summarize(pnl,f'{s} {prov}'); rows.append(r)
print(fmt(rows))
print('\n=== Sep totals: is the consensus "close" in 2017-2019 (aggregators) a genuine close? compare provider totals dispersion ===')
for s in [2017,2018,2019,2022,2024]:
    g=L[(L.season==s)&L.total.notna()].groupby('game_id').total.agg(['std','count']); print(s,'median within-game std of provider totals',g['std'].median(),'mean books',g['count'].mean().round(2))
print('\n=== Sensitivity: define early season as weeks 1-4 / 2-5 / month Sep, thr 2..5 ===')
rows=[]
for nm,mask in [('wk1-4',m.week<=4),('wk2-5',(m.week>=2)&(m.week<=5)),('wk1-5',m.week<=5),('Sep',m.month==9),('Sep+Aug',m.month<=9)]:
    for thr in [2,3,4,5]:
        g=m[mask&(m.tdis.abs()>=thr)]; pnl,_,_=ats_pnl(g.v2_total.values,g.mkt_total.values,g.total.values); r=summarize(pnl,f'{nm} thr>={thr}')
        ss=[g2.pnl.dropna().mean() for _,g2 in g.assign(pnl=pnl).groupby('season')]; r['seasons_pos']=f'{sum(v>0 for v in ss)}/{len(ss)}'; rows.append(r)
print(fmt(rows))
print('\n=== The same cut for 0.5.0-control totals (2021+), and for the V2 MARGIN model in Sep (to see if "Sep" is special generally) ===')
g=m[(m.month==9)&((m.v2_margin-m.mkt_spread_margin).abs()>=3)]; pnl,_,_=ats_pnl(g.v2_margin.values,g.mkt_spread_margin.values,g.margin.values); print(fmt([summarize(pnl,'Sep SPREAD thr>=3')]))
print('\n=== Kalshi-style economics for Sep totals thr>=3: need win% > breakeven. Breakeven at ask 0.52 + fee 0.07*.52*.48=0.0175 -> 0.5375; at ask 0.55 -> 0.567 ===')
dd=d[d.tdis.abs()>=3]; pnl,_,_=ats_pnl(dd.v2_total.values,dd.mkt_total.values,dd.total.values); v=pnl[~np.isnan(pnl)]; wp=(v>0).sum()/((v!=0).sum())
lo,hi=boot_ci((v[v!=0]>0).astype(float)); print(f'win% = {wp:.4f}  CI [{lo:.4f},{hi:.4f}]  n_decided={int((v!=0).sum())}')
