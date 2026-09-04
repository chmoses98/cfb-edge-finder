import numpy as np, pandas as pd
from scipy.stats import norm
import os as _os
RDATA = _os.environ.get("SALVAGE_RDATA", "/home/user/cfb-rdata")  # checkout of the research-data branch
RDATA_V2ENRICH = _os.environ.get("SALVAGE_RDATA_V2ENRICH", "/home/user/cfb-rdata-v2enrich")  # research-data-v2enrich
V2SRC = _os.environ.get("SALVAGE_V2SRC", "/home/user/cfb-v2branch/src")  # claude/cfb-model-v2-research-krupoc src/

rng = np.random.default_rng(20260904)
def boot_ci(x, n=4000, stat=np.mean):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    if len(x) < 5: return (np.nan, np.nan)
    idx = rng.integers(0, len(x), size=(n, len(x)))
    s = stat(x[idx], axis=1) if stat is np.mean else np.array([stat(x[i]) for i in idx])
    return (float(np.quantile(s, 0.025)), float(np.quantile(s, 0.975)))
def ats_pnl(model, line, actual, price=-110):
    """One unit bet on the model's side of the spread vs `line` (home-margin convention). Push=0.
    returns per-game pnl array (nan if no bet)"""
    side = np.sign(model - line)   # +1 = bet home (home covers if actual > line)
    cover = np.sign(actual - line)
    win = side * cover
    payout = 100/abs(price) if price < 0 else price/100
    pnl = np.where(win > 0, payout, np.where(win < 0, -1.0, 0.0))
    pnl = np.where(side == 0, np.nan, pnl)
    return pnl, side, cover
def kalshi_pnl(p_model, p_mkt, outcome, fee=0.07):
    """Buy YES at price p_mkt if p_model>p_mkt else buy NO at (1-p_mkt). One contract, $1 notional.
    outcome 1/0 for YES. Fee 0.07*P*(1-P) per contract on the traded price, charged on entry (taker)."""
    buy_yes = p_model > p_mkt
    price = np.where(buy_yes, p_mkt, 1 - p_mkt)
    won = np.where(buy_yes, outcome == 1, outcome == 0)
    gross = np.where(won, 1 - price, -price)
    f = fee * price * (1 - price)
    return gross - f, price
def summarize(pnl, label=''):
    pnl = np.asarray(pnl, float); v = pnl[~np.isnan(pnl)]
    n = len(v); w = (v > 0).sum(); l = (v < 0).sum(); roi = v.mean() if n else np.nan
    lo, hi = boot_ci(v)
    return dict(label=label, n=n, wins=int(w), losses=int(l), win_pct=(w/(w+l) if (w+l) else np.nan), roi=roi, roi_lo=lo, roi_hi=hi)
def fmt(rows):
    return pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f'{x:.4f}')
