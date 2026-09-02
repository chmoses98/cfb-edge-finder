"""Timing-safe QB features from cfbfastR play-by-play (ESPN-derived, free).

Stage 1: per (season, game_id, team) passer table: dropbacks, EPA, share.
Stage 2: per team-game, features computed from STRICTLY PRIOR games only
(ordered by season, season_type, week). Nothing from the game itself.
"""
import pyarrow.parquet as pq, pandas as pd, numpy as np, glob
COLS=['game_id','week','season','season_type','pos_team','home_team','away_team','passer_player_name','rusher_player_name','pass_attempt','sack','completion','int','EPA','yards_gained','play_type','pass','rush']
rows=[]
for f in sorted(glob.glob('pbp/play_by_play_*.parquet')):
    t=pq.read_table(f, columns=[c for c in COLS if c in pq.read_schema(f).names]).to_pandas()
    t['season']=int(f.split('_')[-1][:4])
    t=t[t.passer_player_name.notna() & (t.play_type!='Timeout')].copy()
    t['db']=1.0
    t['epa']=pd.to_numeric(t.EPA,errors='coerce')
    t['side']=np.where(t.pos_team==t.home_team,'home',np.where(t.pos_team==t.away_team,'away','?'))
    g=t.groupby(['season','season_type','week','game_id','pos_team','side','passer_player_name'],as_index=False).agg(db=('db','sum'),epa=('epa','sum'),cmp=('completion','sum'),ints=('int','sum'),sacks=('sack','sum'))
    rows.append(g); print(f, len(g), flush=True)
P=pd.concat(rows,ignore_index=True)
P['game_id']=P.game_id.astype(str)
P.to_parquet('qb_passers.parquet',index=False)
# team-game level
tg=P.sort_values('db',ascending=False).groupby(['season','season_type','week','game_id','pos_team','side'],as_index=False).agg(
    team_db=('db','sum'),team_epa=('epa','sum'),primary=('passer_player_name','first'),primary_db=('db','first'),primary_epa=('epa','first'),n_passers=('passer_player_name','nunique'))
tg['primary_share']=tg.primary_db/tg.team_db
tg['st_order']=tg.season_type.map({'regular':0,'postseason':1}).fillna(0)
tg=tg.sort_values(['pos_team','season','st_order','week','game_id']).reset_index(drop=True)
# career (prior-only) stats per (team, passer)
key=['pos_team','primary']
tg['prev_primary']=tg.groupby(['pos_team']).primary.shift(1)
tg['prev_season']=tg.groupby(['pos_team']).season.shift(1)
tg['prev_share']=tg.groupby(['pos_team']).primary_share.shift(1)
tg['prev_n_passers']=tg.groupby(['pos_team']).n_passers.shift(1)
# cumulative prior starts/dropbacks/epa for the passer who was primary in the PREVIOUS game (the expected starter)
pp=P.sort_values(['pos_team','passer_player_name','season']).copy()
pp['st_order']=pp.season_type.map({'regular':0,'postseason':1}).fillna(0)
pp=pp.sort_values(['pos_team','passer_player_name','season','st_order','week','game_id'])
grp=pp.groupby(['pos_team','passer_player_name'])
pp['cum_db']=grp.db.cumsum()-pp.db; pp['cum_epa']=grp.epa.cumsum()-pp.epa; pp['cum_games']=grp.cumcount()
pp['cum_db_season']=pp.groupby(['pos_team','passer_player_name','season']).db.cumsum()-pp.db
# per team-game: stats of expected starter (prev game primary) as of BEFORE this game = his cum stats including prev game
# simplest: for each team-game i, look up passer row for (team, prev_primary) in the PREVIOUS game (exists by construction) -> cum after that game
pp_after=pp.copy(); pp_after['cum_db_after']=pp_after.cum_db+pp_after.db; pp_after['cum_epa_after']=pp_after.cum_epa+pp_after.epa; pp_after['cum_games_after']=pp_after.cum_games+1
lk=pp_after[['pos_team','game_id','passer_player_name','cum_db_after','cum_epa_after','cum_games_after']]
tg['prev_game_id']=tg.groupby('pos_team').game_id.shift(1)
tg=tg.merge(lk.rename(columns={'game_id':'prev_game_id','passer_player_name':'prev_primary','cum_db_after':'xqb_career_db','cum_epa_after':'xqb_career_epa','cum_games_after':'xqb_career_starts'}),on=['pos_team','prev_game_id','prev_primary'],how='left')
# season-to-date primary (mode) before this game, and whether prev game's primary differs (starter change signal)
def season_mode(s):
    out=[];seen=[]
    for v in s:
        out.append(pd.Series(seen).mode().iloc[0] if seen else np.nan); seen.append(v)
    return pd.Series(out,index=s.index)
tg['season_primary_so_far']=tg.groupby(['pos_team','season']).primary.transform(season_mode)
tg['games_so_far']=tg.groupby(['pos_team','season']).cumcount()
tg['same_season_prev']=tg.prev_season==tg.season
# features (all from prior games)
tg['qb_new_last_game']=((tg.same_season_prev)&(tg.games_so_far>=2)&(tg.prev_primary!=tg.season_primary_so_far)).astype(float)
tg['qb_prev_share']=tg.prev_share
tg['qb_prev_split']=((tg.prev_share<0.75)).astype(float)
tg['qb_career_starts']=tg.xqb_career_starts.fillna(0)
tg['qb_career_db']=tg.xqb_career_db.fillna(0)
tg['qb_career_epa_db']=(tg.xqb_career_epa.fillna(0))/(tg.xqb_career_db.fillna(0)+150.0)   # shrunk EPA per dropback
tg['qb_exp_log']=np.log1p(tg.qb_career_db)
tg['qb_first_start_flag']=((tg.same_season_prev)&(tg.qb_career_starts<=1)).astype(float)
# prior-season primary QB continuity for Week-1 rows: is prev game (last season) primary == ... unknown; we expose prev-season primary EPA/db and dropbacks
tg['qb_cross_season']=(~tg.same_season_prev).astype(float)
out=tg[['season','game_id','pos_team','side','primary','primary_share','n_passers','prev_primary','qb_new_last_game','qb_prev_share','qb_prev_split','qb_career_starts','qb_career_db','qb_career_epa_db','qb_exp_log','qb_first_start_flag','qb_cross_season','games_so_far']]
out.to_parquet('qb_teamgame.parquet',index=False)
print(out.shape); print(out.describe().T[['mean','std','min','max']].round(3))
print(out[out.season==2024].head(12).to_string())
