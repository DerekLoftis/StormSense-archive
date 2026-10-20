"""Sensitivity of the Tidewatch Skill Score to its weights and normalization.

Reads the scored forecast history the ASA run writes and reports:
  * how strongly the four components move together (redundancy),
  * each station's score and rank under alternative weightings
    (equal, drop-one, emphasize-one, pooled CRITIC),
  * the spread of scores and ranks under 5,000 random weightings
    (Dirichlet(1,1,1,1), i.e. uniform over all weight vectors that sum to 1),
  * the same with site-specific normalization in place of the fixed 1 ft.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import metrics
from .data import station_name

M = metrics.METRICS


def _scores(comp, w):
    w = np.asarray(w, float)
    return comp @ (w / w.sum())


def _rank(series):
    return series.rank(ascending=False, method="min").astype(int)


def scenarios():
    s = {"Equal (25% each)": [0.25] * 4}
    for i, k in enumerate(M):
        w = [1 / 3] * 4
        w[i] = 0
        s[f"Drop {k}"] = w
    for i, k in enumerate(M):
        w = [0.15] * 4
        w[i] = 0.55
        s[f"Emphasize {k} (55%)"] = w
    s["MAE+RMSE share 25% (R2 37.5, Bias 37.5)"] = [0.375, 0.375, 0.125, 0.125]
    return s


def run(history, out_dir, n_samples=5000, seed=7):
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    report = {}

    for norm, prefix in (("generalized", "g_"), ("site", "s_")):
        comp_cols = [prefix + k for k in M]
        h = history.dropna(subset=comp_cols).copy()
        station_comp = h.groupby("station")[comp_cols].mean()
        station_comp.columns = M
        C = station_comp.to_numpy(float)

        # Redundancy between components, across every scored forecast.
        corr = h[comp_cols].corr()
        corr.index = corr.columns = M
        corr.round(3).to_csv(out_dir / f"component_correlation_{norm}.csv")

        # Pooled CRITIC weights (all stations together).
        allc = h[comp_cols].copy()
        allc.columns = M
        sd = allc.std(ddof=0)
        info = sd * (1 - allc.corr().fillna(0)).sum()
        critic = (info / info.sum()).to_dict()

        sc = scenarios()
        sc["Pooled CRITIC " + "/".join(f"{critic[k]:.2f}" for k in M)] = [critic[k] for k in M]
        table = pd.DataFrame(index=station_comp.index)
        table.insert(0, "Name", [station_name(s) for s in table.index])
        table["Forecasts"] = h.groupby("station").size()
        ranks = pd.DataFrame(index=station_comp.index)
        for name, w in sc.items():
            v = pd.Series(_scores(C, w), index=station_comp.index)
            table[name] = v.round(3)
            ranks[name] = _rank(v)
        base_rank = ranks["Equal (25% each)"]
        shift = ranks.sub(base_rank, axis=0).abs()
        table["Max rank change"] = shift.max(axis=1)
        table = table.sort_values("Equal (25% each)", ascending=False)
        table.to_csv(out_dir / f"weight_scenarios_{norm}.csv")

        # Random weights.
        W = rng.dirichlet(np.ones(4), n_samples)
        S = C @ W.T                          # stations x samples
        R = pd.DataFrame(S).rank(ascending=False, method="min").to_numpy()
        eq = _scores(C, [0.25] * 4)
        eq_rank = pd.Series(eq).rank(ascending=False, method="min").to_numpy()
        rho = np.array([np.corrcoef(eq_rank, R[:, j])[0, 1] for j in range(n_samples)])
        # Which weight moves each station's score most: standardized
        # regression coefficient of score on the four weights.
        Wz = (W - W.mean(0)) / W.std(0)
        src = {}
        for i, st in enumerate(station_comp.index):
            y = (S[i] - S[i].mean()) / (S[i].std() or 1)
            beta, *_ = np.linalg.lstsq(Wz, y, rcond=None)
            src[st] = beta
        dist = pd.DataFrame({
            "Name": [station_name(s) for s in station_comp.index],
            "Equal-weight score": eq.round(3),
            "Score p5": np.percentile(S, 5, axis=1).round(3),
            "Score p50": np.percentile(S, 50, axis=1).round(3),
            "Score p95": np.percentile(S, 95, axis=1).round(3),
            "Equal-weight rank": eq_rank.astype(int),
            "Rank p5": np.percentile(R, 5, axis=1).astype(int),
            "Rank p95": np.percentile(R, 95, axis=1).astype(int),
            **{f"Sensitivity to w_{k}": [round(src[s][i], 2) for s in station_comp.index]
               for i, k in enumerate(M)},
        }, index=station_comp.index).sort_values("Equal-weight score", ascending=False)
        dist.to_csv(out_dir / f"random_weights_{norm}.csv")

        _plot_ranges(dist, norm, out_dir / f"station_score_ranges_{norm}.png")
        report[norm] = {
            "corr": corr, "critic": critic, "table": table, "dist": dist,
            "rho_median": float(np.median(rho)), "rho_p5": float(np.percentile(rho, 5)),
            "n_forecasts": len(h), "n_stations": len(station_comp),
        }

    _plot_corr(report, out_dir / "component_correlation.png")
    # Rank agreement between the two normalizations under equal weights.
    g = report["generalized"]["dist"]["Equal-weight rank"]
    s = report["site"]["dist"]["Equal-weight rank"].reindex(g.index)
    report["norm_rank_rho"] = float(np.corrcoef(g, s)[0, 1])
    report["norm_rank_moves"] = (g - s).abs().sort_values(ascending=False)
    return report


def _plot_ranges(dist, norm, path):
    d = dist.iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 0.26 * len(d) + 1.4), dpi=150)
    y = np.arange(len(d))
    ax.hlines(y, d["Score p5"], d["Score p95"], color="#9db7d8", lw=5, label="5–95% of random weightings")
    ax.plot(d["Equal-weight score"], y, "o", color="#1f3b63", ms=5, label="Equal weights (25% each)")
    ax.set_yticks(y)
    ax.set_yticklabels(d.index, fontsize=8)
    ax.set_xlabel("Tidewatch Skill Score")
    label = "sd = window SD of observed (Tidewatch Skill)" if norm == "generalized" else "sd = 30-day SD of observed (site)"
    ax.set_title(f"Score range under random weights · {label}", fontsize=10, loc="left")
    ax.legend(loc="lower left", fontsize=8)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _plot_corr(report, path):
    fig, axes = plt.subplots(1, 2, figsize=(9, 4), dpi=150)
    for ax, norm in zip(axes, ("generalized", "site")):
        c = report[norm]["corr"].to_numpy()
        im = ax.imshow(c, vmin=-1, vmax=1, cmap="RdBu_r")
        ax.set_xticks(range(4))
        ax.set_yticks(range(4))
        ax.set_xticklabels(M)
        ax.set_yticklabels(M)
        ax.grid(False)
        for i in range(4):
            for j in range(4):
                ax.text(j, i, f"{c[i, j]:.2f}", ha="center", va="center", fontsize=9,
                        color="white" if abs(c[i, j]) > 0.6 else "black")
        ax.set_title(f"Component correlation · {norm}", fontsize=10, loc="left")
    fig.colorbar(im, ax=axes, fraction=0.025)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
