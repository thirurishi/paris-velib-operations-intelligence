# Forecasting results

_Trained 2026-11-09T12:07:30+00:00 UTC on data from 2026-11-05 23:00:00+00:00 to 2026-11-08 08:15:00+00:00; tested on 2026-11-08 10:30:00+00:00 → 2026-11-09 12:00:00+00:00 (154,125 station-slots). Time-based split, never random._

> ⚠️ Fewer than 14 days of data: test set is the last 30% of the period. Results are preliminary.

## Bikes available in 1h / 2h (MAE, lower is better)

| Horizon | Method | MAE (bikes) | vs persistence |
|---|---|---|---|
| +1h | Persistence (same as now) | 1.64 | +0.0% |
| +1h | Seasonal (same time last week) | 5.84 | +256.7% |
| +1h | LightGBM | 1.70 | +4.0% |
| +2h | Persistence (same as now) | 2.52 | +0.0% |
| +2h | Seasonal (same time last week) | 5.88 | +133.5% |
| +2h | LightGBM | 2.57 | +2.1% |

## Shortage alerts within 2h

| Event | Method | Base rate | Precision | Recall | Avg precision | Alerts / slot |
|---|---|---|---|---|---|---|
| empty_2h | Rule: <= 2 now | 10.1% | 0.49 | 0.76 | 0.395 | 233.9 |
| empty_2h | LightGBM | 10.1% | 0.78 | 0.50 | 0.712 | 96.6 |
| full_2h | Rule: <= 2 now | 5.9% | 0.48 | 0.71 | 0.361 | 130.3 |
| full_2h | LightGBM | 5.9% | 0.73 | 0.51 | 0.676 | 61.5 |

## What drives the predictions

![SHAP](../docs/img/shap_bikes_2h.png)
![SHAP](../docs/img/shap_empty_2h.png)
