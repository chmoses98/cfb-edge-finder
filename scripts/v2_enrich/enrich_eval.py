"""Enrichment ablation harness on the FROZEN V2 folds.

Reuses cfb_edge_finder.research.v2 (matchup_frame, FEATURE_SETS, run_candidate,
evaluate) unchanged; only appends extra pregame columns to X and to a named
feature set. V2 itself is never modified: baseline candidates are re-run
from the same code so every comparison is like-for-like on identical folds.

Usage: python enrich_eval.py <tag> <enrich.parquet> [col1 col2 ...]
Produces preds/<tag>_{member}.parquet and prints pooled/by-season/segment
and large-disagreement comparisons vs the frozen V2 baseline preds.
"""
import sys, json, numpy as np, pandas as pd
sys.path.insert(0,'/home/user/cfb-edge-finder/src')
from cfb_edge_finder.research.v2 import features as F
from cfb_edge_finder.research.v2.tournament import Candidate, run_candidate, evaluate, DEFAULT_FOLDS
from cfb_edge_finder.research.v2.metrics import paired_delta
pd.set_option('display.width',220)

def load_base():
    df=pd.read_parquet('dataset_d025.parquet'); df['game_id']=df.game_id.astype(str)
    return df

def run(tag, enrich_cols, X, df, members=(('struct+pre',38),('eff+pre',64)), total=False):
    preds={}
    for fs,_ in members:
        name=f'{fs}+{tag}'
        F.FEATURE_SETS[name]=list(F.FEATURE_SETS[fs])+list(enrich_cols)
        cand=Candidate(name=f'{tag}_{fs.replace("+","_")}', model='ridge', target='margin', feature_set=name)
        P,rec=run_candidate(cand, df, X, DEFAULT_FOLDS, verbose=False)
        P['game_id']=P.game_id.astype(str); preds[fs]=P.set_index('game_id')
    ens=preds['struct+pre'][['season','week']].copy()
    ens['pred_margin']=(preds['struct+pre'].pred_margin+preds['eff+pre'].pred_margin)/2
    ens['margin_sd']=(preds['struct+pre'].margin_sd+preds['eff+pre'].margin_sd)/2
    from scipy.stats import norm
    ens['p_home']=1-norm.cdf(-ens.pred_margin/ens.margin_sd); ens['pred_total']=np.nan
    ens=ens.reset_index()
    ens.to_parquet(f'preds/enr_{tag}.parquet',index=False)
    if total:
        fs='tot_eff'; name=f'{fs}+{tag}'; F.FEATURE_SETS[name]=list(F.FEATURE_SETS[fs])+list(enrich_cols)
        cand=Candidate(name=f'{tag}_tot', model='ridge', target='total', feature_set=name)
        T,_=run_candidate(cand, df, X, DEFAULT_FOLDS, verbose=False); T['game_id']=T.game_id.astype(str)
        T.to_parquet(f'preds/enr_{tag}_total.parquet',index=False)
        return ens, T
    return ens, None

def compare(tag, ens, df, T=None):
    base=pd.read_parquet('preds/ens_margin_d025_eq.parquet'); base['game_id']=base.game_id.astype(str)
    mk=pd.read_parquet('preds/market_close.parquet'); mk['game_id']=mk.game_id.astype(str)
    d=df[['game_id','season','week','margin','total','home_won','postseason','neutral','conference_game','mkt_spread_margin','mkt_total']].merge(
        base[['game_id','pred_margin']].rename(columns={'pred_margin':'v2'}),on='game_id').merge(
        ens[['game_id','pred_margin']].rename(columns={'pred_margin':'enr'}),on='game_id')
    d['e_v2']=(d.v2-d.margin).abs(); d['e_en']=(d.enr-d.margin).abs(); d['e_mk']=(d.mkt_spread_margin-d.margin).abs()
    out={'tag':tag}
    def seg(m, label):
        x=d[m]; dl=paired_delta(x.e_v2.values, x.e_en.values, x.game_id.values)
        out[label]={'n':int(len(x)),'v2_mae':round(x.e_v2.mean(),3),'enr_mae':round(x.e_en.mean(),3),'mkt_mae':round(x.e_mk.mean(),3) if x.e_mk.notna().any() else None,'delta':dl}
    seg(np.ones(len(d),bool),'pooled'); seg(d.week<=1,'week_1'); seg(d.week<=3,'weeks_1_3'); seg((d.week>=4)&(~d.postseason.astype(bool)),'weeks_4_plus'); seg(d.postseason.astype(bool),'postseason')
    by={}
    for s,x in d.groupby('season'): by[int(s)]=(round(x.e_v2.mean(),3),round(x.e_en.mean(),3))
    out['by_season']=by
    m=d.mkt_spread_margin.notna(); x=d[m].copy(); x['dis']=x.v2-x.mkt_spread_margin; big=x[x.dis.abs()>=7]
    if len(big):
        toward_truth=(np.sign(big.enr-big.v2)==np.sign(big.margin-big.v2)).mean()
        toward_mkt=(np.sign(big.enr-big.v2)==np.sign(big.mkt_spread_margin-big.v2)).mean()
        side_right_v2=(np.sign(big.margin-big.mkt_spread_margin)==np.sign(big.dis)).mean()
        side_right_en=(np.sign(big.margin-big.mkt_spread_margin)==np.sign(big.enr-big.mkt_spread_margin)).mean()
        out['large_disagreement']={'n':int(len(big)),'v2_mae':round(big.e_v2.mean(),3),'enr_mae':round(big.e_en.mean(),3),'mkt_mae':round(big.e_mk.mean(),3),
            'moved_toward_truth%':round(100*toward_truth,1),'moved_toward_market%':round(100*toward_mkt,1),'v2_side_win%':round(100*side_right_v2,1),'enr_side_win%':round(100*side_right_en,1)}
    if T is not None:
        bt=pd.read_parquet('preds/tot_eff_ridge_d025.parquet'); bt['game_id']=bt.game_id.astype(str)
        t=df[['game_id','total','week','mkt_total']].merge(bt[['game_id','pred_total']].rename(columns={'pred_total':'v2t'}),on='game_id').merge(T[['game_id','pred_total']].rename(columns={'pred_total':'ent'}),on='game_id')
        out['total']={'v2_mae':round((t.v2t-t.total).abs().mean(),3),'enr_mae':round((t.ent-t.total).abs().mean(),3),'mkt_mae':round((t.mkt_total-t.total).abs().mean(),3),
            'delta':paired_delta((t.v2t-t.total).abs().values,(t.ent-t.total).abs().values,t.game_id.values),
            'wk1':(round((t[t.week<=1].v2t-t[t.week<=1].total).abs().mean(),3),round((t[t.week<=1].ent-t[t.week<=1].total).abs().mean(),3))}
    json.dump(out,open(f'eval/enr_{tag}.json','w'),indent=1,default=str)
    print(json.dumps(out,indent=1,default=str))
    return out

if __name__=='__main__':
    tag=sys.argv[1]; enr=pd.read_parquet(sys.argv[2]); enr['game_id']=enr.game_id.astype(str)
    cols=sys.argv[3:] or [c for c in enr.columns if c!='game_id']
    total='--total' in cols; cols=[c for c in cols if c!='--total']
    df=load_base(); X=F.matchup_frame(df); X['game_id']=df.game_id.values
    X=X.merge(enr[['game_id']+cols],on='game_id',how='left').drop(columns='game_id'); X.index=df.index
    print('enrichment coverage:', {c: round(float(X[c].notna().mean()),3) for c in cols})
    ens,T=run(tag, cols, X, df, total=total)
    compare(tag, ens, df, T)
