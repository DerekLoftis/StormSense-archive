"""Forecast statistics and the Tidewatch Skill Score."""
import numpy as np
import pandas as pd

from . import config

METRICS = ["R2", "Bias", "MAE", "RMSE"]


def statistics(paired):
    """R2, bias, MAE, RMSE and both regression lines for one forecast.

    x is observed, y is the Tidewatch prediction (px30).
    R2 is the square of the Pearson correlation, the same value Excel shows on
    a linear trendline. NSE (1 - SSE/SST, what scikit-learn calls r2_score and
    what the first archive_pipeline results report as R2) is kept alongside
    because it also penalizes bias and can go negative.
    """
    x = paired["observed"].to_numpy(float)
    y = paired["predicted"].to_numpy(float)
    n = len(x)
    out = {"N": n}
    if n < 3:
        return {**out, **{k: np.nan for k in
                ["R2", "Bias", "MAE", "RMSE", "SDobs", "NSE", "Slope", "Intercept", "SlopeOrigin", "PeakError"]}}
    err = y - x
    slope, intercept = np.polyfit(x, y, 1)
    r = np.corrcoef(x, y)[0, 1]
    sst = np.sum((x - x.mean()) ** 2)
    out.update(
        R2=r * r,
        Bias=err.mean(),
        MAE=np.abs(err).mean(),
        RMSE=np.sqrt((err ** 2).mean()),
        # Population SD of the observations in the window: sigma in the skill score.
        SDobs=x.std(ddof=0),
        NSE=1 - np.sum(err ** 2) / sst if sst > 0 else np.nan,
        Slope=slope,
        Intercept=intercept,
        SlopeOrigin=np.sum(x * y) / np.sum(x * x),
        # Predicted minus observed highest water in the window: the number a
        # flood forecast user acts on. Used to judge the skill score weights.
        PeakError=y.max() - x.max(),
    )
    return out


def components(stats, sigma_ft):
    """0-1 component scores for R2, bias, MAE and RMSE (Derek's Tidewatch Skill).

    R2 is used as is; each error metric becomes exp(-|metric| / sigma), where
    sigma is the population standard deviation of the observations over the
    evaluation window. A perfect forecast scores 1 on every component.
    """
    if not (sigma_ft and sigma_ft > 0):
        return {k: np.nan for k in METRICS}
    def err_score(v):
        return float(np.exp(-abs(v) / sigma_ft)) if pd.notna(v) else np.nan
    return {
        "R2": float(np.clip(stats["R2"], 0, 1)),
        "Bias": err_score(stats["Bias"]),
        "MAE": err_score(stats["MAE"]),
        "RMSE": err_score(stats["RMSE"]),
    }


def skill_score(stats, sigma_ft, weights=None):
    """Tidewatch Skill S = 0.25 [R2 + e^(-|B|/sd) + e^(-MAE/sd) + e^(-RMSE/sd)], on (0, 1].

    Window-climatology (no-skill) forecasts score about 0.45.
    """
    weights = weights or config.EQUAL_WEIGHTS
    comp = components(stats, sigma_ft)
    if any(pd.isna(comp[k]) for k in METRICS):
        return np.nan
    total = sum(weights[k] for k in METRICS)
    return sum(comp[k] * weights[k] for k in METRICS) / total


def site_scale(observed, end, days=None):
    """Station signal scale: std of observed water level over trailing days."""
    days = days or config.SITE_SCALE_DAYS
    if observed is None or observed.empty:
        return np.nan
    window = observed.loc[end - pd.Timedelta(days=days):end]
    return float(window.std()) if len(window) > 48 else float(observed.std())


def critic_weights(history):
    """Objective per-station weights by the CRITIC method.

    CRITIC (Diakoulaki et al., 1995) weights each criterion by how much it
    varies across the forecasts (its standard deviation) times how much new
    information it carries (sum of 1 - correlation with the other criteria).
    MAE and RMSE move together almost one-for-one, so equal weights count
    "error size" twice; CRITIC gives that pair less combined weight and gives
    bias and R2 more where they actually separate good forecasts from bad.

    history: DataFrame of component scores (columns R2, Bias, MAE, RMSE), one
    row per scored forecast at this station.
    """
    h = history[METRICS].astype(float).dropna()
    n = len(h)
    if n < 4:
        return dict(config.EQUAL_WEIGHTS), n
    sd = h.std(ddof=0)
    corr = h.corr().fillna(0)
    info = sd * (1 - corr).sum()
    if info.sum() <= 0:
        return dict(config.EQUAL_WEIGHTS), n
    w = info / info.sum()
    lam = n / (n + config.CALIBRATION_SHRINKAGE_N)
    shrunk = {k: lam * w[k] + (1 - lam) * config.EQUAL_WEIGHTS[k] for k in METRICS}
    total = sum(shrunk.values())
    return {k: v / total for k, v in shrunk.items()}, n
