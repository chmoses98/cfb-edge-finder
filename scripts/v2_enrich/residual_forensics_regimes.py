import pandas as pd, numpy as np, json, gzip, glob
pd.set_option('display.width',250); pd.set_option('display.max_columns',40)
d = pd.read_parquet('forensic_frame.parquet')
d['kick']=pd.to_datetime(d.kickoff, utc=True, errors='coerce')
# --- coaching hires dated before a bowl (season S+1 coach hired before S postseason game) ---
coach=[]
for f in glob.glob('cache/*/coaches.json.gz'):
    coach+=json.load(gzip.open(f))
c=pd.DataFrame(coach); c['hire']=pd.to_datetime(c.hireDate,utc=True,errors='coerce'); c=c.sort_values('hire').groupby(['school','year'],as_index=False).last()
# for each (school, year) the coach of NEXT year and his hire date
nxt=c[['school','year','coach','hire']].copy(); nxt['year']=nxt.year-1; nxt=nxt.rename(columns={'coach':'next_coach','hire':'next_hire'})
cur=c[['school','year','coach']].rename(columns={'coach':'cur_coach'})
cc=cur.merge(nxt,on=['school','year'],how='left')
cc['change_next']=(cc.next_coach.notna())&(cc.next_coach!=cc.cur_coach)
def flag(side):
    m=d[[side,'season','kick']].merge(cc,left_on=[side,'season'],right_on=['school','year'],how='left')
    return ((m.change_next)&(m.next_hire<m.kick)).values
d['h_lameduck']=flag('home'); d['a_lameduck']=flag('away'); d['lameduck_any']=d.h_lameduck|d.a_lameduck
# --- 2020 abbreviated seasons -> 2021 staleness ---
g20=pd.DataFrame(json.load(gzip.open('cache/2020/games.json.gz')))
played=pd.concat([g20[g20.completed].homeTeam,g20[g20.completed].awayTeam]).value_counts()
d['h_g2020']=d.home.map(played).fillna(0); d['a_g2020']=d.away.map(played).fillna(0)
d['short2020_any']=(d.season==2021)&((d.h_g2020<=5)|(d.a_g2020<=5))
# --- fbs newcomer within 3 seasons ---
tf=[]
for f in glob.glob('cache/*/teams_fbs.json.gz'):
    for r in json.load(gzip.open(f)): tf.append((r.get('school'), int(f.split('/')[1])))
tf=pd.DataFrame(tf,columns=['school','yr']); first=tf.groupby('school').yr.min()
d['h_fbs_age']=d.season-d.home.map(first); d['a_fbs_age']=d.season-d.away.map(first)
d['young_any']=(d.h_fbs_age<=2)|(d.a_fbs_age<=2)
d['academy_any']=d.home.isin(['Army','Navy','Air Force'])|d.away.isin(['Army','Navy','Air Force'])
big=d[d.adis>=7]
def s(g): return pd.Series({'n':len(g),'v2_mae':g.e_v2.abs().mean(),'mk_mae':g.e_mk.abs().mean(),'gap':g.e_v2.abs().mean()-g.e_mk.abs().mean(),'v2_side_win%':100*g.v2_side_right.mean()})
print('== ALL games: gap (V2 MAE - market MAE) by regime ==')
for c_ in ['lameduck_any','short2020_any','young_any','academy_any','postseason','coach_chg_any','fbs_new_any']:
    print(c_); print(d.groupby(c_).apply(s).round(2))
print('\n== 7+ disagreements by regime ==')
for c_ in ['lameduck_any','short2020_any','young_any','academy_any']:
    print(c_); print(big.groupby(c_).apply(s).round(2))
print('\n== postseason 7+: lame-duck vs not ==')
print(big[big.postseason].groupby('lameduck_any').apply(s).round(2))
print('\n== share of total |gap| explained by regimes (all games) ==')
tot_gap=(d.e_v2.abs()-d.e_mk.abs()).sum()
for c_ in ['postseason','lameduck_any','young_any','academy_any','short2020_any','coach_chg_any']:
    sub=d[d[c_]]; print(f'{c_:15s} n={len(sub):5d} share_of_games={len(sub)/len(d):.3f} share_of_gap={(sub.e_v2.abs()-sub.e_mk.abs()).sum()/tot_gap:.3f}')
print('\n== teams where market beats V2 most (all games, >=25 games) ==')
rows=[]
for side in ['home','away']:
    sgn=1 if side=='home' else -1
    t=d.groupby(side).apply(lambda g: pd.Series({'n':len(g),'gap':(g.e_v2.abs()-g.e_mk.abs()).mean(),'v2_bias':sgn*g.e_v2.mean(),'mk_bias':sgn*g.e_mk.mean()}))
    rows.append(t)
t=pd.concat(rows); t=t.groupby(level=0).apply(lambda g: pd.Series({'n':g.n.sum(),'gap':np.average(g.gap,weights=g.n),'v2_bias':np.average(g.v2_bias,weights=g.n),'mk_bias':np.average(g.mk_bias,weights=g.n)}))
print(t[t.n>=25].sort_values('gap',ascending=False).head(15).round(2)); print(t[t.n>=25].sort_values('gap').head(8).round(2))
print('\n== week1 7+ : V2 side ATS record and by season ==')
w=big[big.week<=1]; print(len(w), 'v2 side win%', round(100*w.v2_side_right.mean(),1)); print(w.groupby('season').v2_side_right.agg(['count','mean']).round(2).T)
d.to_parquet('forensic_frame.parquet',index=False)
