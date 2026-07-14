"""Chronological data splits. Enforced everywhere: loaders refuse test data unless
explicitly unlocked, so tuning can never touch it by accident."""

SPLITS = {
    "5m":  {"train": ("2026-02-12", "2026-04-15"),
            "val":   ("2026-04-16", "2026-04-30"),
            "test":  ("2026-05-01", "2026-07-07")},   # 05-01→05-12 + 07-06/07 given gap
    "15m": {"train": ("2025-10-11", "2026-02-28"),
            "val":   ("2026-03-01", "2026-04-15"),
            "test":  ("2026-04-16", "2026-07-07")},
    "1h":  {"train": ("2025-10-11", "2026-03-15"),
            "val":   ("2026-03-16", "2026-05-15"),
            "test":  ("2026-05-16", "2026-07-12")},
    "4h":  {"train": ("2025-10-15", "2026-03-15"),
            "val":   ("2026-03-16", "2026-05-12"),
            "test":  ("2026-05-13", "2026-07-07")},
}

def split_of(family: str, date: str) -> str:
    s = SPLITS[family]
    for name in ("train", "val", "test"):
        lo, hi = s[name]
        if lo <= date <= hi:
            return name
    return "outside"

def filter_split(df, family, splits=("train",), date_col="date"):
    m = df[date_col].map(lambda d: split_of(family, d)).isin(splits)
    return df[m]
