-- Iceberg/Trino path: queries the gold fact table through the Iceberg REST
-- catalog, benefiting from Iceberg metadata (min/max column stats, manifest
-- pruning) and ticker partitioning.
select
    ticker,
    date_trunc('month', trade_date) as month,
    avg(volatility_30d_ann) as avg_monthly_volatility
from iceberg.gold.fct_daily_metrics
where ticker = 'AAPL'
group by ticker, date_trunc('month', trade_date)
order by month
