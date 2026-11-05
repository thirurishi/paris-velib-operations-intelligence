# Data folder (written automatically by the collector)

| Folder | Content | One file per |
|---|---|---|
| `raw/date=YYYY-MM-DD/` | Snapshots of the current UTC day | collector run (every 15 min) |
| `daily/` | All snapshots of a finished UTC day, merged | day |
| `stations/` | Station list: name, lat/lon, capacity | day |

All timestamps are **UTC**. Convert to `Europe/Paris` for analysis.

Sources: Vélib' Métropole open data (© Smovengo / Syndicat Autolib' Vélib' Métropole, ODbL)
and Open-Meteo.com (CC BY 4.0).
