import pandas as pd, numpy as np, json, sys
sys.path.insert(0,'/home/user/cfb-edge-finder/src')
from cfb_edge_finder.research.v2.metrics import paired_delta
pd.set_option('display.width',250); pd.set_option('display.max_columns',40)
df=pd.read_parquet('dataset_d025.parquet',columns=['game_id','season','week','margin','total','home_won','postseason','neutral','conference_game','mkt_spread_margin','mkt_total','mkt_p_home','mkt_spread_open_margin']); df['game_id']=df.game_id.astype(str)
def P(name,col='pred_margin',new=None):
    p=pd.read_parquet(f'preds/{name}.parquet'); p['game_id']=p.game_id.astype(str); return p[['game_id',col]].rename(columns={col:new or name})
d=df.merge(P('ens_margin_d025_eq',new='v2'),on='game_id').merge(P('enr_selfcorr',new='v2sc'),on='game_id')
d=d.merge(P('ens_margin_d025_eq','p_home','v2_p'),on='game_id').merge(P('enr_selfcorr','p_home','v2sc_p'),on='game_id')
d=d.merge(P('tot_eff_ridge_d025_affine','pred_total','v2t'),on='game_id').merge(P('enr_selfcorr2_total','pred_total','v2sct'),on='game_id',how='left')
c=pd.read_csv('control_predictions.csv'); c['game_id']=c.game_id.astype(str); d=d.merge(c[['game_id','ctrl_margin','ctrl_total','ctrl_p_home_closed']],on='game_id',how='left')
# extra regime flags from forensics
f=pd.read_parquet('forensic_frame.parquet',columns=['game_id','lameduck_any','academy_any','young_any','coach_chg_any']); f['game_id']=f.game_id.astype(str); d=d.merge(f,on='game_id',how='left')
qb=pd.read_parquet('enrich_qb.parquet'); qb['game_id']=qb.game_id.astype(str); d=d.merge(qb[['game_id','h_qb_new_last_game','a_qb_new_last_game']],on='game_id',how='left'); d['qb_change_any']=(d.h_qb_new_last_game.fillna(0)+d.a_qb_new_last_game.fillna(0))>0
d['fav']=d.mkt_spread_margin.abs()>=10
def ll(p,y): p=np.clip(p,1e-6,1-1e-6); return -np.mean(y*np.log(p)+(1-y)*np.log(1-p))
def brier(p,y): return np.mean((p-y)**2)
def block(x,label):
    out={'segment':label,'n':len(x)}
    for nm,col in [('0.5.0','ctrl_margin'),('V2','v2'),('V2+selfcorr','v2sc'),('CLOSE','mkt_spread_margin'),('OPEN','mkt_spread_open_margin')]:
        m=x[col].notna(); e=(x[col]-x.margin)[m]
        out[f'{nm} mMAE']=round(e.abs().mean(),3) if m.any() else None; out[f'{nm} bias']=round(e.mean(),2) if m.any() else None
    for nm,col in [('0.5.0','ctrl_total'),('V2','v2t'),('V2+sc','v2sct'),('CLOSE','mkt_total')]:
        m=x[col].notna(); e=(x[col]-x.total)[m]; out[f'{nm} tMAE']=round(e.abs().mean(),3) if m.any() else None
    for nm,col in [('0.5.0','ctrl_p_home_closed'),('V2','v2_p'),('V2+sc','v2sc_p'),('CLOSE','mkt_p_home')]:
        m=x[col].notna(); out[f'{nm} LL']=round(ll(x[col][m].values,x.home_won[m].values.astype(float)),4) if m.any() else None
    return out
common=d[d.ctrl_margin.notna()&d.mkt_spread_margin.notna()]
rows=[block(common,'common 2021-25 (all)')]
for label,m in [('week_1',common.week<=1),('weeks_1_3',common.week<=3),('weeks_4_plus',(common.week>=4)&(~common.postseason.astype(bool))),('postseason',common.postseason.astype(bool)),('neutral',common.neutral.astype(bool)),('favorites>=10',common.fav),('|V2-close|>=7',(common.v2-common.mkt_spread_margin).abs()>=7),('coach_change',common.coach_chg_any==True),('lame-duck',common.lameduck_any==True),('academy',common.academy_any==True),('QB change last game',common.qb_change_any)]:
    rows.append(block(common[m],label))
R=pd.DataFrame(rows); print(R.to_string())
print('\nby season (common):'); print(pd.DataFrame([block(x,str(s)) for s,x in common.groupby('season')]).to_string())
print('\nRMSE (common): 0.5.0 %.3f V2 %.3f V2+sc %.3f CLOSE %.3f' % tuple(np.sqrt(np.mean((common[c]-common.margin)**2)) for c in ['ctrl_margin','v2','v2sc','mkt_spread_margin']))
print('paired V2+sc vs V2 margin (common):', paired_delta((common.v2-common.margin).abs().values,(common.v2sc-common.margin).abs().values,common.game_id.values))
print('paired V2+sc vs CLOSE margin (common):', paired_delta((common.mkt_spread_margin-common.margin).abs().values,(common.v2sc-common.margin).abs().values,common.game_id.values))
allv=d[d.v2.notna()]; print('\nall 8 test seasons: V2 %.3f V2+sc %.3f ; winner LL V2 %.4f V2+sc %.4f Brier %.4f %.4f'%((allv.v2-allv.margin).abs().mean(),(allv.v2sc-allv.margin).abs().mean(),ll(allv.v2_p.values,allv.home_won.values.astype(float)),ll(allv.v2sc_p.values,allv.home_won.values.astype(float)),brier(allv.v2_p.values,allv.home_won.values.astype(float)),brier(allv.v2sc_p.values,allv.home_won.values.astype(float))))
R.to_csv('eval/final_compare.csv',index=False)
