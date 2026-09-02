"""PHASE 13 -- MARKET-INFORMED stacking on persisted out-of-sample predictions.
Chronological: for test season Y, coefficients are fit on seasons < Y that
have the required market columns. Never mixed into PURE FOOTBALL V2."""
import pandas as pd, numpy as np, json
from numpy.linalg import lstsq
d=pd.read_parquet('forensic_frame.parquet')
sc=pd.read_parquet('preds/enr_selfcorr.parquet'); sc['game_id']=sc.game_id.astype(str); d['game_id']=d.game_id.astype(str)
d=d.merge(sc[['game_id','pred_margin']].rename(columns={'pred_margin':'v2sc'}),on='game_id',how='left')
d['open']=d.mkt_spread_open_margin; d['close']=d.mk_m
def fit_pred(train,test,cols):
    X=np.column_stack([np.ones(len(train))]+[train[c].values for c in cols]); b=lstsq(X,train.margin.values,rcond=None)[0]
    Xt=np.column_stack([np.ones(len(test))]+[test[c].values for c in cols]); return Xt@b, b
specs={'open_only':['open'],'v2_only_refit':['v2_m'],'v2+open':['v2_m','open'],'v2sc+open':['v2sc','open'],'close_only':['close'],'v2+close':['v2_m','close'],'v2sc+close':['v2sc','close']}
rows=[]; preds={k:[] for k in specs}
for Y in [2022,2023,2024,2025]:
    tr=d[(d.season<Y)&(d.season>=2021)&d.open.notna()&d.v2sc.notna()]; te=d[(d.season==Y)&d.open.notna()&d.v2sc.notna()]
    r={'season':Y,'n':len(te),'V2':(te.v2_m-te.margin).abs().mean(),'V2sc':(te.v2sc-te.margin).abs().mean(),'OPEN':(te.open-te.margin).abs().mean(),'CLOSE':(te.close-te.margin).abs().mean()}
    for k,cols in specs.items():
        p,b=fit_pred(tr,te,cols); r[k]=np.abs(p-te.margin.values).mean(); r[k+'_coef']=np.round(b[1:],3).tolist()
        preds[k].append(pd.DataFrame({'game_id':te.game_id.values,'season':Y,'p':p}))
    rows.append(r)
R=pd.DataFrame(rows); pd.set_option('display.width',250); pd.set_option('display.max_columns',30)
print(R.round(3).to_string())
allte=d[(d.season>=2022)&d.open.notna()&d.v2sc.notna()].copy()
for k in specs: allte=allte.merge(pd.concat(preds[k]).rename(columns={'p':'p_'+k}),on=['game_id','season'],how='left')
print('\nPOOLED 2022-2025 (n=%d)'%len(allte))
for k in ['V2','V2sc','OPEN','CLOSE']+list(specs):
    col={'V2':'v2_m','V2sc':'v2sc','OPEN':'open','CLOSE':'close'}.get(k,'p_'+k); print(f'  {k:16s} MAE={np.abs(allte[col]-allte.margin).mean():.3f}')
# paired: does V2 add after the opener? v2+open vs open_only
from cfb_edge_finder.research.v2.metrics import paired_delta
e_open=np.abs(allte.p_open_only-allte.margin).values; e_vo=np.abs(allte['p_v2+open']-allte.margin).values; e_vso=np.abs(allte['p_v2sc+open']-allte.margin).values
print('\npaired delta (v2+open) - (open_only):', paired_delta(e_open,e_vo,allte.game_id.values))
print('paired delta (v2sc+open) - (open_only):', paired_delta(e_open,e_vso,allte.game_id.values))
print('paired delta (v2+close) - (close_only):', paired_delta(np.abs(allte.p_close_only-allte.margin).values,np.abs(allte['p_v2+close']-allte.margin).values,allte.game_id.values))
# segments
for name,m in (('week_1',allte.week<=1),('weeks_1_3',allte.week<=3),('wk4+',(allte.week>=4)&(~allte.postseason.astype(bool))),('postseason',allte.postseason.astype(bool)),('|v2-open|>=7',(allte.v2_m-allte.open).abs()>=7)):
    x=allte[m]; print(f'  {name:14s} n={len(x):4d} open={np.abs(x.open-x.margin).mean():.3f} v2+open={np.abs(x["p_v2+open"]-x.margin).mean():.3f} close={np.abs(x.close-x.margin).mean():.3f} v2+close={np.abs(x["p_v2+close"]-x.margin).mean():.3f} v2={np.abs(x.v2_m-x.margin).mean():.3f}')
# does v2+open predict line movement / beat the CLOSE ATS?
allte['mv']=allte.close-allte.open; allte['vo_vs_open']=allte['p_v2+open']-allte.open
big=allte[allte.vo_vs_open.abs()>=2]
print('\nwhen (v2+open) differs from open by >=2 (n=%d): close moved same direction %.1f%%; (v2+open) side vs CLOSE wins %.1f%%'%(len(big),100*(np.sign(big.mv)==np.sign(big.vo_vs_open)).mean(),100*(np.sign(big.margin-big.close)==np.sign(big['p_v2+open']-big.close)).mean()))
allte.to_parquet('market_stack_preds.parquet',index=False)
