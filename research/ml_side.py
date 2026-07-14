"""ML side-selection at pre-open (H7): predict up_won from information available
strictly before window open, walk-forward within train+val.

Features: pre-open quotes/book/flow snapshots (o-30..o-2), Binance momentum/vol,
previous-window outcomes, running 15m context (cross-market), time-of-day.
Model: gradient boosting + logistic baseline. Reports weekly walk-forward accuracy,
AUC, calibration by predicted-prob bucket, and edge vs entry price.

Usage: python3 ml_side.py 5m
"""
import sys, pathlib, warnings
import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from splits import split_of

warnings.filterwarnings("ignore")
MASTER = pathlib.Path(__file__).resolve().parent.parent / "data/master"

PREOPEN_FEATS_BASE = []


def build_features(family):
    df = pd.read_parquet(MASTER / f"{family}_master.parquet")
    df = df[df.up_won >= 0].copy()
    df["split"] = [split_of(family, d) for d in df.date]
    df = df.sort_values("wts").reset_index(drop=True)
    dur = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}[family]

    f = pd.DataFrame(index=df.index)
    # pre-open market state
    for t in ["o-30", "o-10", "o-2"]:
        bid, ask = df[f"bid_{t}"], df[f"ask_{t}"]
        f[f"mid_{t}"] = (bid + ask) / 2 - 0.5
        f[f"spread_{t}"] = ask - bid
        f[f"bszr_{t}"] = np.log1p(df[f"bsz_{t}"]) - np.log1p(df[f"asz_{t}"])
    f["mid_chg"] = f["mid_o-2"] - f["mid_o-30"]
    # book depth (if present)
    if "bid_depth_5c_o-10" in df:
        f["depth_imb"] = (df["bid_depth_5c_o-10"] - df["ask_depth_5c_o-10"]) / (
            df["bid_depth_5c_o-10"] + df["ask_depth_5c_o-10"] + 1)
        f["depth_tot"] = np.log1p(df["bid_depth_5c_o-10"] + df["ask_depth_5c_o-10"])
    # pre-open flow
    for lb in [30, 60, 120]:
        f[f"flow{lb}"] = df.get(f"flow_o0_{lb}")
        f[f"ntl{lb}"] = np.log1p(df.get(f"ntl_o0_{lb}"))
    # Binance
    for r in [5, 15, 30, 60, 120, 300]:
        f[f"bnret{r}"] = df[f"bn_ret{r}_o-2"] * 1e4
    f["bnrv60"] = df["bn_rv60_o-2"] * 1e4
    f["bnrv300"] = df["bn_rv300_o-2"] * 1e4
    f["bntbs"] = df["bn_tbs60_o-2"] - 0.5
    f["bnvol"] = np.log1p(df["bn_vol60_o-2"])
    # previous outcomes (only via consecutive windows)
    consec = df.wts.diff() == dur
    prev1 = df.up_won.shift(1).where(consec)
    prev2 = df.up_won.shift(2).where(consec & consec.shift(1))
    f["prev1"] = prev1 - 0.5
    f["prev2"] = prev2 - 0.5
    f["streak2"] = ((prev1 == prev2) & prev1.notna()).astype(float) * (prev1 - 0.5) * 2
    # previous window's final mid (did it close decisively?)
    pm = (df["bid_c0"] + df["ask_c0"]) / 2
    f["prev_final_mid"] = pm.shift(1).where(consec) - 0.5
    # time of day
    hh = pd.to_datetime(df.wts, unit="s").dt.hour + pd.to_datetime(df.wts, unit="s").dt.minute / 60
    f["tod_sin"] = np.sin(hh / 24 * 2 * np.pi)
    f["tod_cos"] = np.cos(hh / 24 * 2 * np.pi)
    return df, f


def walk_forward(df, f, family, min_train_days=21):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    dev = df[df.split.isin(["train", "val"])]
    dates = sorted(dev.date.unique())
    folds = []
    # weekly refit
    week_starts = dates[min_train_days::7]
    X = f.values
    y = df.up_won.values
    preds = np.full(len(df), np.nan)
    for ws in week_starts:
        tr_idx = df.index[(df.date < ws) & df.split.isin(["train", "val"])]
        te_dates = [d for d in dates if ws <= d][:7]
        te_idx = df.index[df.date.isin(te_dates)]
        if len(tr_idx) < 2000 or not len(te_idx):
            continue
        Xtr = X[tr_idx]
        m = HistGradientBoostingClassifier(max_depth=4, max_iter=150, learning_rate=0.06,
                                           min_samples_leaf=200, l2_regularization=1.0)
        ytr = y[tr_idx]
        ok = ~np.isnan(Xtr).all(axis=1)
        m.fit(np.nan_to_num(Xtr[ok], nan=0.0), ytr[ok])
        preds[te_idx] = m.predict_proba(np.nan_to_num(X[te_idx], nan=0.0))[:, 1]
    df = df.copy()
    df["p_hat"] = preds
    v = df.dropna(subset=["p_hat"])
    v = v[v.split.isin(["train", "val"])]
    from sklearn.metrics import roc_auc_score
    acc = ((v.p_hat > 0.5) == (v.up_won == 1)).mean()
    auc = roc_auc_score(v.up_won, v.p_hat)
    print(f"{family} walk-forward: n={len(v)} acc={acc:.4f} auc={auc:.4f}")
    # calibration + edge vs entry
    v["fav"] = np.where(v.p_hat >= 0.5, v.p_hat, 1 - v.p_hat)
    v["bin"] = pd.cut(v.fav, [0.5, 0.53, 0.56, 0.60, 0.65, 1.0])
    v["hit"] = np.where(v.p_hat >= 0.5, v.up_won, 1 - v.up_won)
    ask_fav = np.where(v.p_hat >= 0.5, v["ask_o-2"], 1 - v["bid_o-2"])
    bid_fav = np.where(v.p_hat >= 0.5, v["bid_o-2"], 1 - v["ask_o-2"])
    v["edge_taker"] = v.hit - ask_fav
    v["edge_maker"] = v.hit - bid_fav  # if we could buy at bid
    print(v.groupby("bin", observed=True).agg(
        n=("hit", "count"), hit=("hit", "mean"),
        edge_taker_c=("edge_taker", lambda x: 100 * x.mean()),
        edge_maker_c=("edge_maker", lambda x: 100 * x.mean())).round(3).to_string())
    return df


if __name__ == "__main__":
    family = sys.argv[1] if len(sys.argv) > 1 else "5m"
    df, f = build_features(family)
    df = walk_forward(df, f, family)
    df[["wts", "date", "split", "up_won", "p_hat"]].to_parquet(
        MASTER / f"ml_side_{family}.parquet", index=False)
