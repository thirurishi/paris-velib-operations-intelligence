# CV bullets (auto-filled with real numbers)

- Built an automated pipeline collecting live data from 1,497 Vélib' stations every 15 minutes (0.4 million records over 5 days) with Python, GitHub Actions, Parquet and DuckDB.
- Found that 20 stations cause 14% of the network's 1,171 daily empty-station hours (SQL/DuckDB, Power BI).
- Forecast station availability 2 hours ahead with LightGBM, reducing error by -2% vs. a persistence baseline; explained drivers with SHAP.
- Designed an OR-Tools truck rebalancing planner (vehicle routing with capacity and time limits), delivered as a live Streamlit app and a Power BI dashboard.
