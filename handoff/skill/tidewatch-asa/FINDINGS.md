# ASA and skill score: first results (6 Oct 2026, rescored 7 Oct with Derek's formula)

Data: Lexie Chen's September captures (now in `tidewatch-archive`), plus
Derek's five nor'easter captures (24 to 29 Sep) and his 30-day observation
files through 4 Oct, copied from his Desktop into
`input/derek_sept2026_noreaster/`. That gives 13 Tidewatch runs from
16 Sep to 29 Sep with full 36-hour observations at 34 stations, so 441
scored forecasts. Six of the runs fall in the nor'easter (23 to 29 Sep).
That is still a small sample, so read the numbers as early evidence.

## 1. The nor'easter in numbers

| | Calm runs (16-20 Sep) | Nor'easter runs (23-29 Sep) |
|---|---|---|
| Mean RMSE | 0.24 ft | 0.45 ft |
| Mean bias | -0.02 ft | +0.08 ft |
| Mean peak error (forecast max - observed max) | -0.07 ft | +0.12 ft |
| Mean R² | 0.90 | 0.81 |
| Mean Tidewatch Skill (0-1) | 0.748 | 0.672 |

Tidewatch overpredicted the flood peaks. The run issued 25 Sep 17Z put the
peak 0.40 ft too high on average across stations, and 0.59 ft too high at
Sewells Point (`cycles/2026-09-25_1700Z/`). At Sewells Point, the four
nor'easter runs from 23 to 25 Sep all ran high at the peak, by 0.23 to
0.59 ft (skill 0.72 to 0.78). The runs from 28 and 29 Sep, on the way
down, scored 0.911 and 0.857.

## 2. Dahlgren, Washington and Silver Beach: Tidewatch's own data is crossed

DAHL, WADC and USSB looked fine against Tidewatch's observations but not
against the NOAA API (Lexie's results).

The aliasing check (`output/aliasing/ALIASING.md`) explains why:
- Tidewatch publishes another gauge's observations under these station IDs.
- Their astronomical tide predictions match the other gauge too.
- The station numbers in `stations.py` were right.

Their scores here measure Tidewatch against the copied gauge, not the named site.

## 3. The four components still move together

With Derek's exponential form and the window SD as σ, the error components
correlate strongly with one another across all 441 forecasts:

| Pair | Correlation |
|---|---|
| Bias, MAE and RMSE with each other | 0.91 to 0.99 |
| R² with the error components | 0.69 to 0.82 |

Under equal weights the score is still mostly error size.

## 4. Which component tracks the flood peak

The number a flood forecast user acts on is the peak error. The correlation of
each component with smaller |peak error| is:

| Component | σ = window SD (Tidewatch Skill) | σ = 30-day SD (Site Skill) |
|---|---|---|
| R² | 0.23 | 0.23 |
| e^(−\|Bias\|/σ) | 0.42 | 0.48 |
| e^(−MAE/σ) | 0.44 | 0.52 |
| e^(−RMSE/σ) | 0.44 | 0.51 |
| Score | 0.41 | 0.46 |

R² still says the least about peak error. CRITIC weights computed on all the
forecasts would put the most weight on R²:

| Component | CRITIC weight |
|---|---|
| R² | 0.38 |
| Bias | 0.30 |
| MAE | 0.16 |
| RMSE | 0.16 |

That would move the score away from what matters for floods. So for a
storm-tide score, my suggestion stands:
- merge MAE and RMSE into one term;
- give R² the smallest share.

Dividing by the window SD also has a side effect. The same 0.3 ft miss costs
more on a small tide than on a large one or a surge. That is why the storm
runs lose less skill than their RMSE alone suggests (window SD 0.78 ft vs
0.62 ft calm).

## 5. Rankings

Under 5,000 random weightings, station rankings stay close to the equal-weight
ranking (median Spearman 0.995). The window SD and the 30-day SD rank stations
almost identically (0.994).

Two groups score low:
- **Near non-tidal sites** score very low under Derek's formula, because a few
  tenths of a foot of error is large against their tiny SD: USNW 0.199,
  USPF 0.188, USBB 0.182. Their observations are also held values
  (ALIASING.md).
- **Small-range Potomac and Rappahannock gauges** come out at the bottom of
  the NOAA stations despite modest RMSE: LEWI 0.640 (RMSE 0.27 ft) and WMPT
  0.670 (RMSE 0.23 ft).

## Open

* Fit per-station weights directly to peak error once the live archive (6-hourly
  since 6 Oct) has a few weeks of forecasts. `PeakError` is already in the
  history.
