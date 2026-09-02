import pandas as pd, numpy as np, json
df = pd.read_parquet('dataset_d025.parquet')
v2 = pd.read_parquet('preds/ens_margin_d025_eq.parquet')[['game_id','pred_margin','p_home']].rename(columns={'pred_margin':'v2_m','p_home':'v2_p'})
vt = pd.read_parquet('preds/tot_eff_ridge_d025_affine.parquet')[['game_id','pred_total']].rename(columns={'pred_total':'v2_t'})
d = df.merge(v2,on='game_id').merge(vt,on='game_id')
d = d[d.completed & d.both_fbs & d.mkt_spread_margin.notna()].copy()
d['mk_m']=d.mkt_spread_margin; d['mk_t']=d.mkt_total
d['e_v2']=d.v2_m-d.margin; d['e_mk']=d.mk_m-d.margin
d['dis']=d.v2_m-d.mk_m; d['adis']=d.dis.abs()
# who was right: sign of (actual - market) vs sign of (v2 - market)
d['v2_side_right']=np.sign(d.margin-d.mk_m)==np.sign(d.dis)
d['pre_talent_diff']=d.h_pre_talent-d.a_pre_talent
d['ret_ppa_diff']=d.h_pre_ret_percentPPA-d.a_pre_ret_percentPPA
d['coach_chg_any']=(d.h_pre_coach_change.fillna(0)>0)|(d.a_pre_coach_change.fillna(0)>0)
d['fbs_new_any']=(d.h_pre_fbs_new.fillna(0)>0)|(d.a_pre_fbs_new.fillna(0)>0)
d['fav_size']=d.mk_m.abs()
d['rest_diff']=d.home_rest_days-d.away_rest_days
d['elo_diff']=d.home_pregame_elo-d.away_pregame_elo
d['v2_minus_elo']=np.nan
print('games with closing spread:', len(d), 'seasons', sorted(d.season.unique()))
print('V2 MAE %.3f  market MAE %.3f  corr %.3f' % (d.e_v2.abs().mean(), d.e_mk.abs().mean(), d[['v2_m','mk_m']].corr().iloc[0,1]))
bins=[0,3,5,7,10,99]; labels=['0-3','3-5','5-7','7-10','10+']
d['bucket']=pd.cut(d.adis,bins,labels=labels,right=False)
def summ(g):
    return pd.Series({'games':len(g),'v2_mae':g.e_v2.abs().mean(),'mk_mae':g.e_mk.abs().mean(),
        'v2_bias':g.e_v2.mean(),'mk_bias':g.e_mk.mean(),'v2_side_win%':100*g.v2_side_right.mean(),
        'v2_tot_mae':(g.v2_t-g.total).abs().mean(),'mk_tot_mae':(g.mk_t-g.total).abs().mean(),
        'wk1%':100*(g.week<=1).mean(),'wk1-3%':100*(g.week<=3).mean(),'post%':100*g.postseason.mean(),
        'neutral%':100*g.neutral.mean(),'conf%':100*g.conference_game.mean(),'coachchg%':100*g.coach_chg_any.mean(),
        'fbsnew%':100*g.fbs_new_any.mean(),'fav_size':g.fav_size.mean(),'tot_level':g.mk_t.mean(),
        '|talent_diff|':g.pre_talent_diff.abs().mean(),'|ret_ppa_diff|':g.ret_ppa_diff.abs().mean(),
        '|rest_diff|':g.rest_diff.abs().mean(),'v2_fav_more%':100*((d.loc[g.index,'v2_m'].abs()>d.loc[g.index,'mk_m'].abs())).mean()})
pd.set_option('display.width',250); pd.set_option('display.max_columns',40)
print('\n== BY |V2-MARKET| DISAGREEMENT ==')
print(d.groupby('bucket',observed=True).apply(summ).round(2).T)
print('\n== 7+ bucket by season ==')
big=d[d.adis>=7]
print(big.groupby('season').apply(lambda g: pd.Series({'n':len(g),'v2_mae':g.e_v2.abs().mean(),'mk_mae':g.e_mk.abs().mean(),'v2_side_win%':100*g.v2_side_right.mean()})).round(2))
print('\n== 7+ bucket by week bucket ==')
big['wb']=pd.cut(big.week,[0,1,3,8,15,99],labels=['wk1','wk2-3','wk4-8','wk9-15','post'])
print(big.groupby('wb',observed=True).apply(lambda g: pd.Series({'n':len(g),'v2_mae':g.e_v2.abs().mean(),'mk_mae':g.e_mk.abs().mean(),'v2_side_win%':100*g.v2_side_right.mean(),'v2_bias':g.e_v2.mean()})).round(2))
print('\n== direction: does V2 disagree by liking the FAVORITE more or less than market? (7+) ==')
big['v2_more_fav']=big.v2_m.abs()>big.mk_m.abs()
print(big.groupby('v2_more_fav').apply(lambda g: pd.Series({'n':len(g),'v2_mae':g.e_v2.abs().mean(),'mk_mae':g.e_mk.abs().mean(),'v2_side_win%':100*g.v2_side_right.mean()})).round(2))
print('\n== 7+: does the pregame Elo side with V2 or the market? ==')
big['elo_m']=(big.elo_diff/25.0)  # crude: 25 elo ~ 1 pt
big['elo_with_v2']=np.sign(big.elo_m-big.mk_m)==np.sign(big.dis)
print(big.groupby('elo_with_v2').apply(lambda g: pd.Series({'n':len(g),'v2_side_win%':100*g.v2_side_right.mean(),'v2_mae':g.e_v2.abs().mean(),'mk_mae':g.e_mk.abs().mean()})).round(2))
print('\n== 7+ by coaching change / fbs_new / neutral / postseason ==')
for c in ['coach_chg_any','fbs_new_any','neutral','postseason','conference_game']:
    print(c); print(big.groupby(c).apply(lambda g: pd.Series({'n':len(g),'v2_mae':g.e_v2.abs().mean(),'mk_mae':g.e_mk.abs().mean(),'v2_side_win%':100*g.v2_side_right.mean()})).round(2))
print('\n== largest 40 disagreements ==')
cols=['season','week','home','away','v2_m','mk_m','mkt_spread_open_margin','margin','v2_side_right','h_pre_coach_change','a_pre_coach_change','h_pre_ret_percentPassingPPA','a_pre_ret_percentPassingPPA','home_rest_days','away_rest_days']
print(big.sort_values('adis',ascending=False)[cols].head(40).round(2).to_string())
d.to_parquet('forensic_frame.parquet',index=False)
