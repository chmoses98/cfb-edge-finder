"""Environment family from Open-Meteo kickoff weather (fetched by scripts/v2_fetch_weather.py).
Three products = three timing classes; each evaluated separately on totals and margin."""
import pandas as pd, numpy as np, subprocess, sys
df=pd.read_parquet('dataset_d025.parquet',columns=['game_id','season','venue_dome']); df['game_id']=df.game_id.astype(str)
for prod in ['archive','hforecast','prevrun']:
    try: w=pd.read_parquet(f'weather/weather_{prod}.parquet')
    except Exception as e: print(prod,'missing',e); continue
    w['game_id']=w.game_id.astype(str)
    e=df.merge(w,on='game_id',how='left')
    dome=e.venue_dome.fillna(False).astype(bool)
    out=pd.DataFrame({'game_id':e.game_id})
    out[f'wx_wind_{prod}']=np.where(dome,0.0,e.get('wind_speed_10m_kick'))
    out[f'wx_gust_{prod}']=np.where(dome,0.0,e.get('wind_gusts_10m_max3h'))
    out[f'wx_precip_{prod}']=np.where(dome,0.0,e.get('precipitation_sum3h'))
    out[f'wx_temp_{prod}']=np.where(dome,20.0,e.get('temperature_2m_kick'))
    out[f'wx_cold_{prod}']=np.where(dome,0.0,(pd.to_numeric(e.get('temperature_2m_kick'),errors='coerce')<5).astype(float))
    out[f'wx_wind15_{prod}']=np.where(dome,0.0,(pd.to_numeric(e.get('wind_speed_10m_kick'),errors='coerce')>=24).astype(float))  # >=15 mph
    out[f'wx_rain_{prod}']=np.where(dome,0.0,(pd.to_numeric(e.get('precipitation_sum3h'),errors='coerce')>=1.0).astype(float))
    print(prod,'coverage',round(w.game_id.isin(df.game_id).mean(),3),'games',len(w), out.describe().T[['mean','max']].round(2).to_string())
    out.to_parquet(f'enrich_wx_{prod}.parquet',index=False)
    cols=[c for c in out.columns if c!='game_id']
    subprocess.run([sys.executable,'enrich_eval.py',f'wx_{prod}',f'enrich_wx_{prod}.parquet']+cols+['--total'],stdout=open(f'eval/log_wx_{prod}.txt','w'),stderr=subprocess.STDOUT)
    import json; o=json.load(open(f'eval/enr_wx_{prod}.json'))
    for k in ['pooled','week_1','weeks_4_plus']:
        v=o[k]; d=v['delta']; print(f"  margin {k:13s} n={v['n']} v2={v['v2_mae']} enr={v['enr_mae']} delta={d.get('mean',d.get('delta')):+.3f} ci={[round(x,3) for x in d.get('ci95',[])]}")
    print('  TOTAL', o.get('total')); print('  by_season', o['by_season'])
