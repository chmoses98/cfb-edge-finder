import numpy as np, pandas as pd, gzip, json
from common import *
import os as _os
RDATA = _os.environ.get("SALVAGE_RDATA", "/home/user/cfb-rdata")  # checkout of the research-data branch
RDATA_V2ENRICH = _os.environ.get("SALVAGE_RDATA_V2ENRICH", "/home/user/cfb-rdata-v2enrich")  # research-data-v2enrich
V2SRC = _os.environ.get("SALVAGE_V2SRC", "/home/user/cfb-v2branch/src")  # claude/cfb-model-v2-research-krupoc src/

m = pd.read_parquet('master.parquet')
P5 = {'SEC','Big Ten','Big 12','ACC','Pac-12','Big 10'}
m['home_p5']=m.home_conf.isin(P5); m['away_p5']=m.away_conf.isin(P5)
m['both_p5']=m.home_p5&m.away_p5; m['both_g5']=~m.home_p5&~m.away_p5; m['mixed']=m.home_p5^m.away_p5
m['abs_close']=m.mkt_spread_margin.abs()
m['dis']=m.v2_margin-m.mkt_spread_margin; m['absdis']=m.dis.abs()
m['comp_dis']=(m.v2_struct_margin-m.v2_eff_margin).abs()
m['v050_gap']=(m.v2_margin-m.v050_margin).abs()
m['talent_diff']=(m.h_pre_talent-m.a_pre_talent).abs()
m['coach_change_any']=(m.h_pre_coach_change.fillna(0)>0)|(m.a_pre_coach_change.fillna(0)>0)
m['ret_pass_min']=np.minimum(m.h_pre_ret_percentPassingPPA,m.a_pre_ret_percentPassingPPA)
m['ret_pass_max']=np.maximum(m.h_pre_ret_percentPassingPPA,m.a_pre_ret_percentPassingPPA)
m['ret_ppa_min']=np.minimum(m.h_pre_ret_percentPPA,m.a_pre_ret_percentPPA)
m['min_games']=np.minimum(m.home_games_so_far,m.away_games_so_far)
m['month']=pd.to_datetime(m.kickoff).dt.month
# portal counts (2021+) from cache: transfers OUT per team-season by 'origin'
portal={}
for y in range(2021,2026):
    try: rows=json.load(gzip.open(f'{RDATA}/data/research_cache/v2/{y}/portal.json.gz'))
    except FileNotFoundError: continue
    for r in rows:
        for k in ('origin','destination'):
            t=r.get(k)
            if t: portal[(y,t)]=portal.get((y,t),0)+1
m['portal_h']=[portal.get((s,h),np.nan) if s>=2021 else np.nan for s,h in zip(m.season,m.home)]
m['portal_a']=[portal.get((s,a),np.nan) if s>=2021 else np.nan for s,a in zip(m.season,m.away)]
m['portal_max']=np.maximum(m.portal_h,m.portal_a)
# weather (2025 partial) hforecast
try:
    w=pd.read_parquet(RDATA_V2ENRICH + '/data/research_cache/v2_enrich/weather/weather_prevrun.parquet'); print('weather cols',list(w.columns)[:30], len(w))
except Exception as e: print('weather load fail',e); w=None
cuts = {
 'ALL': m.index==m.index,
 'home fav (close)': m.mkt_spread_margin>0, 'away fav': m.mkt_spread_margin<0,
 'V2 side = home': m.dis>0, 'V2 side = away': m.dis<0,
 'V2 side = fav': np.sign(m.dis)==np.sign(m.mkt_spread_margin), 'V2 side = dog': np.sign(m.dis)==-np.sign(m.mkt_spread_margin),
 'close |spread|<3': m.abs_close<3, 'close 3-7': (m.abs_close>=3)&(m.abs_close<7), 'close 7-14': (m.abs_close>=7)&(m.abs_close<14), 'close 14-21': (m.abs_close>=14)&(m.abs_close<21), 'close 21+': m.abs_close>=21,
 'both P5': m.both_p5, 'both G5': m.both_g5, 'P5 vs G5': m.mixed,
 'conference game': m.conference_game, 'non-conference': ~m.conference_game, 'neutral': m.neutral, 'postseason': m.postseason,
 'week 1': m.week==1, 'weeks 1-3': m.week<=3, 'weeks 4-8': (m.week>=4)&(m.week<=8), 'weeks 9+': m.week>=9,
 'Sep': m.month==9, 'Oct': m.month==10, 'Nov': m.month==11, 'Dec/Jan': m.month.isin([12,1]),
 'coach change (either)': m.coach_change_any, 'no coach change': ~m.coach_change_any,
 'low ret passing (min<0.35)': m.ret_pass_min<0.35, 'high ret passing (min>0.7)': m.ret_pass_min>0.7,
 'low continuity (min retPPA<0.5)': m.ret_ppa_min<0.5, 'high continuity (min retPPA>0.7)': m.ret_ppa_min>0.7,
 'transfer heavy (max portal>=25)': m.portal_max>=25, 'transfer light (max portal<12)': m.portal_max<12,
 'talent gap large (>200)': m.talent_diff>200, 'talent gap small (<80)': m.talent_diff<80,
 'FBS newcomer involved': (m.h_pre_fbs_new.fillna(0)>0)|(m.a_pre_fbs_new.fillna(0)>0),
 'components agree (<2)': m.comp_dis<2, 'components disagree (>=4)': m.comp_dis>=4,
 'V2 far from 0.5.0 (>=5)': m.v050_gap>=5, 'V2 near 0.5.0 (<2)': m.v050_gap<2,
 'dome': m.venue_dome==1, 'high elevation (>1200m)': m.venue_elev_m>1200,
 'short rest (<=6d either)': (m.home_rest_days<=6)|(m.away_rest_days<=6), 'bye week (>=13d either)': (m.home_rest_days>=13)|(m.away_rest_days>=13),
 'long travel away (>1500km)': m.away_travel_km>1500,
 'late kick (>=23 UTC)': m.kick_hour_utc>=23, 'early kick (<17 UTC)': m.kick_hour_utc<17,
 'V2 p_home extreme (>0.9 or <0.1)': (m.v2_p_home>0.9)|(m.v2_p_home<0.1),
}
def run(m, cuts, thr, target='spread'):
    rows=[]
    for name,mask in cuts.items():
        d=m[mask.fillna(False) if hasattr(mask,'fillna') else mask]
        if target=='spread':
            d=d[d.absdis>=thr]; pnl,side,_=ats_pnl(d.v2_margin.values,d.mkt_spread_margin.values,d.margin.values)
        else:
            d=d[(d.v2_total-d.mkt_total).abs()>=thr]; pnl,side,_=ats_pnl(d.v2_total.values,d.mkt_total.values,d.total.values)
        r=summarize(pnl,name)
        if r['n']<50: continue
        # seasons positive
        ss=[]
        for s,g in d.assign(pnl=pnl).groupby('season'):
            v=g.pnl.dropna(); ss.append(v.mean() if len(v)>=20 else np.nan)
        ss=np.array(ss); r['seasons_pos']=f'{int(np.nansum(ss>0))}/{int(np.sum(~np.isnan(ss)))}'
        # z-score for win pct vs 0.5 (excl pushes)
        wl=r['wins']+r['losses']; r['z']=(r['wins']-wl/2)/np.sqrt(wl/4) if wl else np.nan
        rows.append(r)
    df=pd.DataFrame(rows)
    return df
for target in ['spread','total']:
    for thr in [0,3]:
        df=run(m,cuts,thr,target)
        print(f'\n===== {target.upper()} vs close, |disagreement|>={thr}: {len(df)} cuts examined; Bonferroni z for p<0.05 across {len(df)*4} cuts ≈ {abs(norm.ppf(0.025/(len(df)*4))):.2f} =====')
        print(df.sort_values('roi',ascending=False).to_string(index=False,float_format=lambda x:f'{x:.4f}'))
