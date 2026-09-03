"""Weather / environment test on the games Open-Meteo actually delivered (2025 only,
time-guarded partial fetch). The rolling-origin ablation harness is not meaningful
with one partial season, so this is a residual test: does kickoff weather explain
V2's total/margin residuals, and does the closing market already price it?
Class per product: archive=B (observed), hforecast=A- (archived short-lead forecast),
prevrun=A (day-ahead forecast)."""
import pandas as pd, numpy as np, json
from scipy import stats
df=pd.read_parquet('dataset_d025.parquet',columns=['game_id','season','week','total','margin','venue_dome','postseason'])
df['game_id']=df.game_id.astype(str)
v2t=pd.read_parquet('preds/tot_eff_ridge_d025_affine.parquet')[['game_id','pred_total']].rename(columns={'pred_total':'v2_total'})
v2m=pd.read_parquet('preds/ens_margin_d025_eq.parquet')[['game_id','pred_margin']].rename(columns={'pred_margin':'v2_margin'})
mk=pd.read_parquet('preds/market_close.parquet')[['game_id','pred_total','pred_margin']].rename(columns={'pred_total':'mkt_total','pred_margin':'mkt_margin'})
for d in (v2t,v2m,mk): d['game_id']=d.game_id.astype(str)
base=df.merge(v2t,on='game_id').merge(v2m,on='game_id').merge(mk,on='game_id')
base=base[base.total.notna()&base.mkt_total.notna()]
out={}
def ci_mean(x):
    x=np.asarray(x,float); x=x[~np.isnan(x)]
    if len(x)<5: return [None,None,None,int(len(x))]
    se=x.std(ddof=1)/np.sqrt(len(x)); return [round(x.mean(),2),round(x.mean()-1.96*se,2),round(x.mean()+1.96*se,2),int(len(x))]
for prod,cls in [('archive','B_observed_proxy'),('hforecast','A_minus_archived_forecast'),('prevrun','A_day_ahead_forecast')]:
    w=pd.read_parquet(f'weather/weather_{prod}.parquet'); w['game_id']=w.game_id.astype(str)
    e=base.merge(w[['game_id','wind_speed_10m_kick','wind_gusts_10m_max3h','precipitation_sum3h','temperature_2m_kick']],on='game_id')
    e=e[~e.venue_dome.fillna(False).astype(bool)]
    e['r_v2_tot']=e.total-e.v2_total; e['r_mkt_tot']=e.total-e.mkt_total; e['v2_minus_mkt_tot']=e.v2_total-e.mkt_total
    e['r_v2_mar']=e.margin-e.v2_margin; e['r_mkt_mar']=e.margin-e.mkt_margin
    e['windy']=e.wind_speed_10m_kick>=24; e['gusty']=e.wind_gusts_10m_max3h>=40; e['rain']=e.precipitation_sum3h>=1.0; e['hot']=e.temperature_2m_kick>=30
    res={'timing_class':cls,'n_games_with_lines':int(len(e)),'seasons':sorted(e.season.unique().tolist()),
         'v2_total_mae':round(e.r_v2_tot.abs().mean(),3),'mkt_total_mae':round(e.r_mkt_tot.abs().mean(),3)}
    for col in ['wind_speed_10m_kick','wind_gusts_10m_max3h','precipitation_sum3h','temperature_2m_kick']:
        x=e[col].astype(float)
        res[f'corr_{col}']={k:[round(stats.spearmanr(x,e[k],nan_policy='omit')[0],3),round(stats.spearmanr(x,e[k],nan_policy='omit')[1],3)] for k in ['r_v2_tot','r_mkt_tot','v2_minus_mkt_tot','r_v2_mar']}
    for flag in ['windy','gusty','rain','hot']:
        m=e[e[flag]]
        res[f'{flag}_games']={'n':int(len(m)),'v2_total_resid_mean':ci_mean(m.r_v2_tot),'mkt_total_resid_mean':ci_mean(m.r_mkt_tot),'v2_minus_mkt_total_mean':ci_mean(m.v2_minus_mkt_tot),
                              'v2_total_mae':round(m.r_v2_tot.abs().mean(),2) if len(m) else None,'mkt_total_mae':round(m.r_mkt_tot.abs().mean(),2) if len(m) else None,
                              'v2_margin_resid_mean':ci_mean(m.r_v2_mar)}
    # simple OLS: V2 total residual ~ wind + rain (does weather explain what V2 misses?) and same for the market
    X=np.column_stack([np.ones(len(e)),e.wind_speed_10m_kick.astype(float),e.precipitation_sum3h.astype(float).clip(upper=20)])
    for tgt in ['r_v2_tot','r_mkt_tot']:
        y=e[tgt].values.astype(float); ok=~np.isnan(X).any(1)&~np.isnan(y)
        b,_,_,_=np.linalg.lstsq(X[ok],y[ok],rcond=None); resid=y[ok]-X[ok]@b; n,k=ok.sum(),3
        se=np.sqrt(np.diag(np.linalg.inv(X[ok].T@X[ok])*(resid@resid)/(n-k)))
        res[f'ols_{tgt}']={'wind_kmh_coef':round(b[1],3),'wind_t':round(b[1]/se[1],2),'rain_mm_coef':round(b[2],3),'rain_t':round(b[2]/se[2],2),'n':int(n)}
    out[prod]=res
    print(f"== {prod} ({cls}) n={len(e)} v2 tot MAE {res['v2_total_mae']} mkt {res['mkt_total_mae']}")
    for flag in ['windy','gusty','rain','hot']: print('  ',flag,res[f'{flag}_games'])
    print('   corr wind:',res['corr_wind_speed_10m_kick'],' corr rain:',res['corr_precipitation_sum3h'])
    print('   ols v2:',res['ols_r_v2_tot'],' ols mkt:',res['ols_r_mkt_tot'])
out['_note']='Time-guarded partial fetch: only part of the 2025 season (newest-first) was delivered before the 290-min guard; Open-Meteo throttled to ~2.3 requests/min per runner. Residual test on games with closing lines; not a rolling-origin ablation.'
json.dump(out,open('eval/enr_weather_residual.json','w'),indent=1)
