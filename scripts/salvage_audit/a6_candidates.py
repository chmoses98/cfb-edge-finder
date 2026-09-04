import numpy as np, pandas as pd
from common import *
import os as _os
RDATA = _os.environ.get("SALVAGE_RDATA", "/home/user/cfb-rdata")  # checkout of the research-data branch
RDATA_V2ENRICH = _os.environ.get("SALVAGE_RDATA_V2ENRICH", "/home/user/cfb-rdata-v2enrich")  # research-data-v2enrich
V2SRC = _os.environ.get("SALVAGE_V2SRC", "/home/user/cfb-v2branch/src")  # claude/cfb-model-v2-research-krupoc src/

m = pd.read_parquet('master.parquet'); L=pd.read_parquet('lines.parquet')
m['month']=pd.to_datetime(m.kickoff).dt.month
m['tdis']=m.v2_total-m.mkt_total; m['dis']=m.v2_margin-m.mkt_spread_margin
print('################ CANDIDATE 1: September totals, |V2-close|>=3 ################')
d=m[(m.month==9)].copy()
for thr in [0,1,2,3,4,5,6,8]:
    dd=d[d.tdis.abs()>=thr]; pnl,side,_=ats_pnl(dd.v2_total.values,dd.mkt_total.values,dd.total.values)
    r=summarize(pnl,f'Sep thr>={thr}'); ro=summarize(pnl[side>0],'  over'); ru=summarize(pnl[side<0],'  under')
    print(fmt([r,ro,ru]))
print('-- per season Sep thr>=3, with over/under split --')
rows=[]
for s,g in d[d.tdis.abs()>=3].groupby('season'):
    pnl,side,_=ats_pnl(g.v2_total.values,g.mkt_total.values,g.total.values); r=summarize(pnl,str(s)); r['over_n']=int((side>0).sum()); r['over_wp']=np.nanmean(pnl[side>0]>0) if (side>0).sum() else np.nan; r['under_wp']=np.nanmean(pnl[side<0]>0) if (side<0).sum() else np.nan; rows.append(r)
print(fmt(rows))
print('-- by week (Sep) thr>=3 --')
rows=[]
for wk,g in d[d.tdis.abs()>=3].groupby('week'):
    pnl,side,_=ats_pnl(g.v2_total.values,g.mkt_total.values,g.total.values); rows.append(summarize(pnl,f'week {wk}'))
print(fmt(rows))
print('-- by month, all seasons, thr>=3: is Sep special or is it an early-season gradient? --')
rows=[]
for mo,g in m[m.tdis.abs()>=3].groupby('month'):
    pnl,side,_=ats_pnl(g.v2_total.values,g.mkt_total.values,g.total.values); rows.append(summarize(pnl,f'month {mo}'))
print(fmt(rows))
print('-- V2 total bias and market total bias in Sep vs rest (actual - pred) --')
for nm,g in [('Sep',m[m.month==9]),('Oct+',m[m.month>9])]:
    print(nm, 'V2 bias %.2f MAE %.2f | mkt bias %.2f MAE %.2f | V2-mkt mean %.2f' % ((g.total-g.v2_total).mean(),(g.total-g.v2_total).abs().mean(),(g.total-g.mkt_total).mean(),(g.total-g.mkt_total).abs().mean(),(g.v2_total-g.mkt_total).mean()))
print('-- Sep thr>=3: influence -- drop top-k most favourable games (largest pnl not informative since binary); instead: leave-one-season-out ROI --')
dd=d[d.tdis.abs()>=3]; pnl,side,_=ats_pnl(dd.v2_total.values,dd.mkt_total.values,dd.total.values); dd=dd.assign(pnl=pnl)
for s in sorted(dd.season.unique()):
    v=dd[dd.season!=s].pnl.dropna(); print(f'  drop {s}: n={len(v)} roi={v.mean():.4f} wp={(v>0).mean()/((v!=0).mean()):.4f}')
print('-- Sep thr>=3 at per-book CLOSE totals (that book), -110 --')
LB=L.dropna(subset=['total']).rename(columns={'total':'book_total'}).merge(m[['game_id','season','v2_total','total','month']],on=['game_id','season']); LB=LB[LB.month==9]
rows=[]
for prov,g in LB.groupby('provider'):
    g=g[(g.v2_total-g.book_total).abs()>=3]
    if len(g)<80: continue
    pnl,side,_=ats_pnl(g.v2_total.values,g.book_total.values,g.total.values); rows.append(summarize(pnl,prov))
print(fmt(rows))
print('-- Sep thr>=3 at OPEN totals (consensus of books with open), settle vs actual --')
mo=m[(m.month==9)].copy()
LBo=L.dropna(subset=['total_open']).groupby(['game_id','season']).total_open.median().reset_index()
mo=mo.merge(LBo,on=['game_id','season'])
for thr in [2,3,5]:
    g=mo[(mo.v2_total-mo.total_open).abs()>=thr]; pnl,side,_=ats_pnl(g.v2_total.values,g.total_open.values,g.total.values); r=summarize(pnl,f'Sep @open thr>={thr}')
    mv=g.mkt_total-g.total_open; vo=g.v2_total-g.total_open; r['moved_with']=((np.sign(mv)==np.sign(vo))&(mv!=0)).mean(); r['moved_against']=((np.sign(mv)==-np.sign(vo))&(mv!=0)).mean(); print(fmt([r]))
print('-- Is Sep-totals signal explained by a simple rule the market could have? e.g. Sep totals: bet UNDER always / bet direction of raw (pre-affine) V2 --')
for nm,g in [('Sep all: always UNDER',d),('Sep all: always OVER',d)]:
    pnl,_,_=ats_pnl(np.full(len(g),-999.0) if 'UNDER' in nm else np.full(len(g),999.0),g.mkt_total.values,g.total.values); print(fmt([summarize(pnl,nm)]))
# 0.5.0 control totals in Sep (2021+)
g=d.dropna(subset=['ctrl_total']); g=g[(g.ctrl_total-g.mkt_total).abs()>=3]; pnl,_,_=ats_pnl(g.ctrl_total.values,g.mkt_total.values,g.total.values); print(fmt([summarize(pnl,'0.5.0/ctrl total Sep thr>=3 (2021+)')]))
g=d[d.season>=2021]; g=g[(g.v2_total-g.mkt_total).abs()>=3]; pnl,_,_=ats_pnl(g.v2_total.values,g.mkt_total.values,g.total.values); print(fmt([summarize(pnl,'V2 total Sep thr>=3 (2021+ only)')]))
g=d[(d.v2_total_raw-d.mkt_total).abs()>=3]; pnl,_,_=ats_pnl(g.v2_total_raw.values,g.mkt_total.values,g.total.values); print(fmt([summarize(pnl,'V2 RAW (pre-affine) total Sep thr>=3')]))

print('\n################ CANDIDATE 2: Week 1 spreads ################')
d=m[m.week==1].copy()
for thr in [0,2,3,5,7]:
    dd=d[d.dis.abs()>=thr]; pnl,side,_=ats_pnl(dd.v2_margin.values,dd.mkt_spread_margin.values,dd.margin.values)
    fav=np.sign(dd.dis.values)==np.sign(dd.mkt_spread_margin.values)
    print(fmt([summarize(pnl,f'wk1 thr>={thr}'),summarize(pnl[fav],'  V2 on fav'),summarize(pnl[~fav],'  V2 on dog'),summarize(pnl[side>0],'  V2 on home'),summarize(pnl[side<0],'  V2 on away')]))
print('-- per season wk1 thr>=0 --'); rows=[]
for s,g in d.groupby('season'):
    pnl,side,_=ats_pnl(g.v2_margin.values,g.mkt_spread_margin.values,g.margin.values); rows.append(summarize(pnl,str(s)))
print(fmt(rows))
print('-- weeks 1,2,3,4 separately thr>=0 --'); rows=[]
for wk,g in m[m.week<=4].groupby('week'):
    pnl,side,_=ats_pnl(g.v2_margin.values,g.mkt_spread_margin.values,g.margin.values); rows.append(summarize(pnl,f'week {wk}'))
print(fmt(rows))
print('-- wk1: 0.5.0 control vs close (2021+), and V2 members --')
g=d.dropna(subset=['v050_margin']); pnl,_,_=ats_pnl(g.v050_margin.values,g.mkt_spread_margin.values,g.margin.values); print(fmt([summarize(pnl,'0.5.0 wk1 all')]))
g=d[d.season>=2021]; pnl,_,_=ats_pnl(g.v2_margin.values,g.mkt_spread_margin.values,g.margin.values); print(fmt([summarize(pnl,'V2 wk1 2021+')]))
for c in ['v2_struct_margin','v2_eff_margin']:
    pnl,_,_=ats_pnl(d[c].values,d.mkt_spread_margin.values,d.margin.values); print(fmt([summarize(pnl,f'{c} wk1 all')]))
print('-- wk1 at OPEN (2021+) --')
g=d.dropna(subset=['mkt_spread_open_margin']); pnl,_,_=ats_pnl(g.v2_margin.values,g.mkt_spread_open_margin.values,g.margin.values); r=summarize(pnl,'wk1 @open'); mv=g.mkt_spread_margin-g.mkt_spread_open_margin; vo=g.v2_margin-g.mkt_spread_open_margin; r['moved_with']=((np.sign(mv)==np.sign(vo))&(mv!=0)).mean(); r['moved_against']=((np.sign(mv)==-np.sign(vo))&(mv!=0)).mean(); print(fmt([r]))
print('-- wk1 MAE: V2 %.2f open %.2f close %.2f' % ((d.margin-d.v2_margin).abs().mean(), (g.margin-g.mkt_spread_open_margin).abs().mean(), (d.margin-d.mkt_spread_margin).abs().mean()))
print('-- wk1 margin bias (actual-pred): V2 %.2f close %.2f ; wk1 home-fav share %.2f' % ((d.margin-d.v2_margin).mean(), (d.margin-d.mkt_spread_margin).mean(), (d.mkt_spread_margin>0).mean()))
