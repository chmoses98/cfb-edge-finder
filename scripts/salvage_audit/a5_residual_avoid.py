import numpy as np, pandas as pd, sys
from common import *
import os as _os
RDATA = _os.environ.get("SALVAGE_RDATA", "/home/user/cfb-rdata")  # checkout of the research-data branch
RDATA_V2ENRICH = _os.environ.get("SALVAGE_RDATA_V2ENRICH", "/home/user/cfb-rdata-v2enrich")  # research-data-v2enrich
V2SRC = _os.environ.get("SALVAGE_V2SRC", "/home/user/cfb-v2branch/src")  # claude/cfb-model-v2-research-krupoc src/

sys.path.insert(0,V2SRC)
from cfb_edge_finder.research.v2.features import matchup_frame, FEATURE_SETS
m=pd.read_parquet('master.parquet')
df=pd.read_parquet(RDATA + '/data/research/v2/dataset_d025.parquet')
df=df[df.both_fbs & df.mkt_spread_margin.notna() & df.margin.notna() & (df.season>=2015)].copy()
X=matchup_frame(df)
feats=list(dict.fromkeys(FEATURE_SETS['struct+pre']+FEATURE_SETS['eff+pre']))
feats=[f for f in feats if f in X.columns]
X=X[feats].astype(float)
X['mkt_margin']=df.mkt_spread_margin.values; X['abs_mkt']=np.abs(df.mkt_spread_margin.values); X['home_fav']=(df.mkt_spread_margin.values>0).astype(float)
y=(df.margin-df.mkt_spread_margin).values  # market residual
seasons=df.season.values
print('=== Walk-forward ridge on MARKET RESIDUAL (actual - close) with V2 features + market line; alpha via inner val season ===')
from numpy.linalg import solve
def ridge(Xtr,ytr,alpha):
    mu=Xtr.mean(0); sd=Xtr.std(0)+1e-9; Z=(Xtr-mu)/sd; b=solve(Z.T@Z+alpha*np.eye(Z.shape[1]),Z.T@(ytr-ytr.mean())); return mu,sd,b,ytr.mean()
def pred(Xte,mu,sd,b,c): return ((Xte-mu)/sd)@b+c
out=[]
Xv=X.fillna(X.median()).values
for yr in sorted(set(seasons)):
    if yr<2017: continue
    tr=seasons<yr; va=seasons==(yr-1 if yr!=2021 else 2019); trr=tr&~va; te=seasons==yr
    best=None
    for a in [10,100,1000,10000,100000]:
        p=pred(Xv[va],*ridge(Xv[trr],y[trr],a)); mae=np.abs(y[va]-p).mean()
        if best is None or mae<best[0]: best=(mae,a)
    p=pred(Xv[te],*ridge(Xv[tr],y[tr],best[1]))
    out.append(pd.DataFrame({'game_id':df.game_id.values[te],'season':yr,'resid_pred':p,'resid':y[te],'mkt':df.mkt_spread_margin.values[te],'margin':df.margin.values[te]}))
    print(f'  {yr}: alpha={best[1]} val_mae={best[0]:.3f} test corr(pred,resid)={np.corrcoef(p,y[te])[0,1]:.3f} sd(pred)={p.std():.2f}')
R=pd.concat(out)
print('pooled corr(resid_pred, resid) = %.4f ; MAE close %.4f vs close+resid_model %.4f' % (np.corrcoef(R.resid_pred,R.resid)[0,1], np.abs(R.resid).mean(), np.abs(R.resid-R.resid_pred).mean()))
rows=[]
for thr in [0,0.5,1,1.5,2,3]:
    d=R[R.resid_pred.abs()>=thr]; pnl,_,_=ats_pnl(d.mkt.values+d.resid_pred.values,d.mkt.values,d.margin.values); r=summarize(pnl,f'|resid_pred|>={thr}')
    ss=[]; 
    for s,g in d.assign(pnl=pnl).groupby('season'): ss.append(g.pnl.dropna().mean())
    r['seasons_pos']=f'{sum(v>0 for v in ss)}/{len(ss)}'; rows.append(r)
print(fmt(rows))
print('\n=== "AVOID" value: does |V2-close| predict market absolute error? (market residual magnitude) ===')
m['absdis']=(m.v2_margin-m.mkt_spread_margin).abs(); m['mkt_abs_err']=(m.margin-m.mkt_spread_margin).abs()
m['bin']=pd.cut(m.absdis,[0,1,2,3,5,7,10,99])
print(m.groupby('bin',observed=True).agg(n=('game_id','size'),mkt_mae=('mkt_abs_err','mean'),v2_sd=('v2_sd_m','mean'),abs_close=('mkt_spread_margin',lambda s: s.abs().mean())).round(2).to_string())
print('corr(|V2-close|, market abs error) = %.3f ; corr(v2_sd, market abs error) = %.3f ; corr(|close|, mkt abs err)=%.3f' % (np.corrcoef(m.absdis,m.mkt_abs_err)[0,1], np.corrcoef(m.v2_sd_m,m.mkt_abs_err)[0,1], np.corrcoef(m.mkt_spread_margin.abs(),m.mkt_abs_err)[0,1]))
# does |V2-close| add to a model of market abs error beyond |close| and sd?
Xa=np.column_stack([np.ones(len(m)),m.mkt_spread_margin.abs(),m.v2_sd_m,m.absdis]); ya=m.mkt_abs_err.values
b=np.linalg.lstsq(Xa,ya,rcond=None)[0]; print('mkt_abs_err ~ 1 + |close| + v2_sd + |v2-close| coefs',b.round(3))
print('\n=== Calibration aid: does blending V2 into market improve probability of covering? (walk-forward logistic on cover ~ (v2-close)) ===')
d=m.copy(); d['cover']=(d.margin>d.mkt_spread_margin).astype(float); d=d[d.margin!=d.mkt_spread_margin]
from scipy.special import expit
res=[]
for yr in sorted(d.season.unique()):
    tr=d[d.season<yr]; te=d[d.season==yr]
    if len(tr)<500: continue
    # 1-param logistic fit by grid on slope k: P(cover)=expit(k*(v2-close))
    ks=np.linspace(-0.1,0.1,201); x=(tr.v2_margin-tr.mkt_spread_margin).values; yy=tr.cover.values
    lls=[-(yy*np.log(expit(k*x)+1e-9)+(1-yy)*np.log(1-expit(k*x)+1e-9)).mean() for k in ks]; k=ks[int(np.argmin(lls))]
    xt=(te.v2_margin-te.mkt_spread_margin).values; p=expit(k*xt); ll_model=-(te.cover.values*np.log(p)+(1-te.cover.values)*np.log(1-p)).mean(); ll_half=np.log(2)
    res.append((yr,k,ll_model,ll_half)); print(f'  {yr}: k={k:+.4f} LL(model)={ll_model:.4f} vs LL(0.5)={ll_half:.4f} -> implied P(cover) at 5pt disagreement={expit(k*5):.3f}')
