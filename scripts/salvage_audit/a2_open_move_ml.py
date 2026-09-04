import numpy as np, pandas as pd
from common import *
import os as _os
RDATA = _os.environ.get("SALVAGE_RDATA", "/home/user/cfb-rdata")  # checkout of the research-data branch
RDATA_V2ENRICH = _os.environ.get("SALVAGE_RDATA_V2ENRICH", "/home/user/cfb-rdata-v2enrich")  # research-data-v2enrich
V2SRC = _os.environ.get("SALVAGE_V2SRC", "/home/user/cfb-v2branch/src")  # claude/cfb-model-v2-research-krupoc src/

m = pd.read_parquet('master.parquet'); L = pd.read_parquet('lines.parquet')
# ================= D. OPENING LINE & MOVEMENT =================
print('=== D. Opening line (consensus median of books with spreadOpen; 2021+) ===')
d = m.dropna(subset=['mkt_spread_open_margin']).copy()
d['v2_open']=d.v2_margin-d.mkt_spread_open_margin; d['move']=d.mkt_spread_margin-d.mkt_spread_open_margin
d['v2_close']=d.v2_margin-d.mkt_spread_margin
print('n',len(d), d.groupby('season').size().to_dict())
print('MAE  V2 %.3f  open %.3f  close %.3f' % ((d.margin-d.v2_margin).abs().mean(),(d.margin-d.mkt_spread_open_margin).abs().mean(),(d.margin-d.mkt_spread_margin).abs().mean()))
print('\n-- ATS betting the V2 side AT THE OPENER (settled vs actual), -110 --')
rows=[]
for thr in [0,2,3,5,7]:
    dd=d[d.v2_open.abs()>=thr]; pnl,_,_=ats_pnl(dd.v2_margin.values,dd.mkt_spread_open_margin.values,dd.margin.values); rows.append(summarize(pnl,f'open thr>={thr}'))
print(fmt(rows))
print('\n-- Does (V2 - open) predict (close - open)? --')
for thr in [0,2,3,5,7]:
    dd=d[d.v2_open.abs()>=thr]
    agree=(np.sign(dd.move)==np.sign(dd.v2_open))&(dd.move!=0); nomove=(dd.move==0)
    x=dd.v2_open.values; yv=dd.move.values; slope=np.polyfit(x,yv,1)[0]; r=np.corrcoef(x,yv)[0,1]
    # CLV in points: signed move in V2 direction
    clv=np.sign(dd.v2_open.values)*dd.move.values
    print(f'thr>={thr}: n={len(dd)} moved-with-V2={agree.mean():.3f} no-move={nomove.mean():.3f} moved-against={(1-agree.mean()-nomove.mean()):.3f} slope={slope:.3f} corr={r:.3f} mean CLV pts={clv.mean():.3f} CI={boot_ci(clv)}')
print('per season slope of move on (v2-open), all games; and moved-with share at thr>=3')
for s,dd in d.groupby('season'):
    x=dd.v2_open.values; yv=dd.move.values
    d3=dd[dd.v2_open.abs()>=3]; agree=((np.sign(d3.move)==np.sign(d3.v2_open))&(d3.move!=0)).mean(); against=((np.sign(d3.move)==-np.sign(d3.v2_open))&(d3.move!=0)).mean()
    clv=np.sign(d3.v2_open.values)*d3.move.values
    print(f'  {s}: n={len(dd)} slope={np.polyfit(x,yv,1)[0]:.3f} corr={np.corrcoef(x,yv)[0,1]:.3f} | thr3 n={len(d3)} with={agree:.3f} against={against:.3f} CLV={clv.mean():.3f}')
print('\n-- Is the movement information already in the opener? Regress move on (v2-open) AND on open itself / features --')
X=np.column_stack([np.ones(len(d)),d.v2_open.values,d.mkt_spread_open_margin.values]); b=np.linalg.lstsq(X,d.move.values,rcond=None)[0]
print('move ~ 1 + (v2-open) + open : coefs', b.round(4))
# Does the opener itself (via regression to the mean) explain moves? residual after accounting for V2? compare R2
def r2(X,y): b=np.linalg.lstsq(X,y,rcond=None)[0]; return 1-((y-X@b)**2).sum()/((y-y.mean())**2).sum()
y=d.move.values
print('R2 move~v2_open: %.4f | move~open: %.4f | both: %.4f' % (r2(np.column_stack([np.ones(len(d)),d.v2_open.values]),y), r2(np.column_stack([np.ones(len(d)),d.mkt_spread_open_margin.values]),y), r2(X,y)))
print('\n-- CLV vs. actual: among V2-side-at-open bets with thr>=3, split by whether market moved toward V2 --')
d3=d[d.v2_open.abs()>=3].copy(); pnl,_,_=ats_pnl(d3.v2_margin.values,d3.mkt_spread_open_margin.values,d3.margin.values); d3['pnl']=pnl
d3['with']=np.where((np.sign(d3.move)==np.sign(d3.v2_open))&(d3.move!=0),'with',np.where(d3.move==0,'none','against'))
print(fmt([summarize(g.pnl,k) for k,g in d3.groupby('with')]))
print('\n-- Reverse: bet the V2 side vs CLOSE only when market has ALREADY moved toward V2 (steam-follow), thr on |v2-close| --')
rows=[]
for thr in [0,2,3,5]:
    dd=d[(d.v2_close.abs()>=thr)]
    withm=dd[(np.sign(dd.move)==np.sign(dd.v2_close))&(dd.move!=0)]; agm=dd[(np.sign(dd.move)==-np.sign(dd.v2_close))&(dd.move!=0)]
    for nm,g in [('moved toward V2',withm),('moved away from V2',agm)]:
        pnl,_,_=ats_pnl(g.v2_margin.values,g.mkt_spread_margin.values,g.margin.values); rows.append(summarize(pnl,f'|v2-close|>={thr} {nm}'))
print(fmt(rows))
# ---- per-book open->close, book-specific (open at that book, close at that book) ----
print('\n-- Per book: V2 side at that book OPEN, ATS at -110; and movement share --')
LB=L.dropna(subset=['spread_open','spread']).merge(m[['game_id','season','v2_margin','margin']],on=['game_id','season'])
rows=[]
for (prov),g in LB.groupby('provider'):
    if len(g)<300: continue
    g=g[(g.v2_margin-g.mkt_margin_open).abs()>=3]
    pnl,_,_=ats_pnl(g.v2_margin.values,g.mkt_margin_open.values,g.margin.values); r=summarize(pnl,f'{prov} thr3 @open'); 
    mv=g.mkt_margin-g.mkt_margin_open; vo=g.v2_margin-g.mkt_margin_open
    r['moved_with']=((np.sign(mv)==np.sign(vo))&(mv!=0)).mean(); r['moved_against']=((np.sign(mv)==-np.sign(vo))&(mv!=0)).mean(); rows.append(r)
print(fmt(rows))
# ================= E. STACKING (walk-forward) =================
print('\n=== E. Walk-forward stacking: margin ~ a + b*close + c*V2 ; margin ~ open + V2 ; total ~ close + V2 ===')
def wf_stack(d, cols, target):
    out=[]
    for y in sorted(d.season.unique()):
        tr=d[d.season<y]; te=d[d.season==y]
        if len(tr)<500: continue
        Xtr=np.column_stack([np.ones(len(tr))]+[tr[c].values for c in cols]); Xte=np.column_stack([np.ones(len(te))]+[te[c].values for c in cols])
        b=np.linalg.lstsq(Xtr,tr[target].values,rcond=None)[0]
        out.append(pd.DataFrame({'season':y,'pred':Xte@b,'actual':te[target].values,'game_id':te.game_id.values}))
        print(f'   {y} coefs {dict(zip(["c"]+cols,b.round(3)))}')
    return pd.concat(out)
s1=wf_stack(m,['mkt_spread_margin'],'margin'); s2=wf_stack(m,['mkt_spread_margin','v2_margin'],'margin')
print('close only MAE %.4f | close+V2 MAE %.4f | paired diff %.4f CI %s' % ((s1.actual-s1.pred).abs().mean(),(s2.actual-s2.pred).abs().mean(),((s2.actual-s2.pred).abs()-(s1.actual-s1.pred).abs()).mean(), boot_ci((s2.actual-s2.pred).abs().values-(s1.actual-s1.pred).abs().values)))
d=m.dropna(subset=['mkt_spread_open_margin'])
s1=wf_stack(d,['mkt_spread_open_margin'],'margin'); s2=wf_stack(d,['mkt_spread_open_margin','v2_margin'],'margin')
print('open only MAE %.4f | open+V2 MAE %.4f | paired diff %.4f CI %s' % ((s1.actual-s1.pred).abs().mean(),(s2.actual-s2.pred).abs().mean(),((s2.actual-s2.pred).abs()-(s1.actual-s1.pred).abs()).mean(), boot_ci((s2.actual-s2.pred).abs().values-(s1.actual-s1.pred).abs().values)))
s1=wf_stack(m,['mkt_total'],'total'); s2=wf_stack(m,['mkt_total','v2_total'],'total')
print('total close only MAE %.4f | +V2 MAE %.4f | paired diff %.4f CI %s' % ((s1.actual-s1.pred).abs().mean(),(s2.actual-s2.pred).abs().mean(),((s2.actual-s2.pred).abs()-(s1.actual-s1.pred).abs()).mean(), boot_ci((s2.actual-s2.pred).abs().values-(s1.actual-s1.pred).abs().values)))
# ================= F. MONEYLINE at REAL per-book prices =================
print('\n=== F. Moneyline at REAL book prices (vig included). Bet 1 unit when V2 EV>0 with margin; best price across books, and per-book ===')
ML=L.dropna(subset=['hml','aml']).merge(m[['game_id','season','v2_p_home','home_won','mkt_p_home','v050_p_home_closed']],on=['game_id','season'])
def dec(ml): return 1+ml/100 if ml>0 else 1+100/(-ml)
ML['dec_h']=ML.hml.map(dec); ML['dec_a']=ML.aml.map(dec)
ML['ev_h']=ML.v2_p_home*ML.dec_h-1; ML['ev_a']=(1-ML.v2_p_home)*ML.dec_a-1
# best price per game
best=ML.groupby('game_id').agg(season=('season','first'),dec_h=('dec_h','max'),dec_a=('dec_a','max'),p=('v2_p_home','first'),y=('home_won','first'),p050=('v050_p_home_closed','first')).reset_index()
def ml_pnl(df, pcol='p', ev_thr=0.0):
    ev_h=df[pcol]*df.dec_h-1; ev_a=(1-df[pcol])*df.dec_a-1
    side=np.where(ev_h>ev_a,'H','A'); ev=np.maximum(ev_h,ev_a)
    dec_=np.where(side=='H',df.dec_h,df.dec_a); won=np.where(side=='H',df.y==1,df.y==0)
    pnl=np.where(won,dec_-1,-1.0); pnl=np.where(ev>=ev_thr,pnl,np.nan)
    return pnl, side, ev, dec_
rows=[]
for thr in [0,0.02,0.05,0.10,0.15,0.20]:
    pnl,side,ev,dec_=ml_pnl(best,'p',thr); r=summarize(pnl,f'best-price EV>={thr}'); r['fav_share']=np.nanmean(np.where(np.isnan(pnl),np.nan,(dec_<2.0))); rows.append(r)
print(fmt(rows))
print('-- split by favourite (dec<2) vs dog at EV>=0.05 --')
pnl,side,ev,dec_=ml_pnl(best,'p',0.05); 
print(fmt([summarize(pnl[dec_<2.0],'favs'),summarize(pnl[dec_>=2.0],'dogs')]))
print('-- per season EV>=0.05 best price --')
print(fmt([summarize(ml_pnl(g,'p',0.05)[0],str(s)) for s,g in best.groupby('season')]))
print('-- per book (that book\'s own price), EV>=0.05 --')
rows=[]
for prov,g in ML.groupby('provider'):
    if len(g)<300: continue
    gg=g.rename(columns={'v2_p_home':'p','home_won':'y'}); pnl,_,_,_=ml_pnl(gg,'p',0.05); rows.append(summarize(pnl,prov))
print(fmt(rows))
print('-- 0.5.0 control on same best-price set, EV>=0.05 --')
b2=best.dropna(subset=['p050']); print(fmt([summarize(ml_pnl(b2,'p050',0.05)[0],'0.5.0 EV>=0.05'),summarize(ml_pnl(b2,'p050',0.0)[0],'0.5.0 EV>=0')]))
print('-- mean overround per book --'); print(ML.groupby('provider').overround.mean().round(4).to_dict())
