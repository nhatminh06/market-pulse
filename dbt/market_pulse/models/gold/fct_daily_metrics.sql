{{ config(properties={"partitioning": "ARRAY['ticker']", "format": "'PARQUET'"}) }}

with base as (
    select *,
        lag(adj_close) over (partition by ticker order by trade_date) as prev_close
    from {{ ref('stg_prices') }}
),
metrics as (
    select
        ticker,
        trade_date,
        adj_close,
        volume,
        (adj_close / nullif(prev_close, 0)) - 1 as daily_return,
        avg(adj_close) over (partition by ticker order by trade_date
            rows between 19 preceding and current row)  as sma_20,
        avg(adj_close) over (partition by ticker order by trade_date
            rows between 49 preceding and current row)  as sma_50,
        avg(adj_close) over (partition by ticker order by trade_date
            rows between 199 preceding and current row) as sma_200,
        stddev((adj_close / nullif(prev_close,0)) - 1) over (partition by ticker order by trade_date
            rows between 29 preceding and current row) * sqrt(252) as volatility_30d_ann,
        (volume - avg(volume) over (partition by ticker order by trade_date
            rows between 29 preceding and current row))
        / nullif(stddev(volume) over (partition by ticker order by trade_date
            rows between 29 preceding and current row), 0) as volume_zscore
    from base
)
select *,
    case when sma_50 >= sma_200 then 'bullish' else 'bearish' end as trend_regime,
    abs(volume_zscore) > 3 as is_volume_anomaly
from metrics