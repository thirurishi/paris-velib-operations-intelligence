# Forecasting results

_Trained 2026-11-23T13:41:41+00:00 UTC on data from 2026-11-05 23:00:00+00:00 to 2026-11-15 12:15:00+00:00; tested on 2026-11-16 00:00:00+00:00 → 2026-11-23 13:30:00+00:00 (1,031,724 station-slots). Time-based split, never random._

## Bikes available in 1h / 2h (MAE, lower is better)

| Horizon | Method | MAE (bikes) | vs persistence |
|---|---|---|---|
| +1h | Persistence (same as now) | 1.69 | +0.0% |
| +1h | Seasonal (last week, else yesterday) | 6.12 | +261.3% |
| +1h | LightGBM | 1.60 | -5.4% |
| +2h | Persistence (same as now) | 2.62 | +0.0% |
| +2h | Seasonal (last week, else yesterday) | 6.11 | +133.8% |
| +2h | LightGBM | 2.31 | -11.8% |

## Shortage alerts within 2h

| Event | Method | Base rate | Precision | Recall | Avg precision | Alerts / slot |
|---|---|---|---|---|---|---|
| empty_2h | Rule: <= 2 now | 10.7% | 0.48 | 0.77 | 0.39 | 255.2 |
| empty_2h | LightGBM | 10.7% | 0.78 | 0.54 | 0.748 | 111.0 |
| full_2h | Rule: <= 2 now | 6.2% | 0.50 | 0.72 | 0.379 | 133.1 |
| full_2h | LightGBM | 6.2% | 0.75 | 0.60 | 0.746 | 73.5 |

## What drives the predictions

![SHAP](../docs/img/shap_bikes_2h.png)
![SHAP](../docs/img/shap_empty_2h.png)
