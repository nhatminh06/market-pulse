{{ config(properties={"partitioning": "ARRAY['ticker']", "format": "'PARQUET'"}) }}

with src as (
    select * from {{ source('bronze', 'prices') }}
),
cleaned as (
    select
        ticker,
        cast(date as date)        as trade_date,
        cast(open as double)      as open,
        cast(high as double)      as high,
        cast(low as double)       as low,
        cast(close as double)     as close,
        cast(adj_close as double) as adj_close,
        cast(volume as bigint)    as volume
    from src
    where close is not null and close > 0 and volume >= 0
)
-- dedupe: keep one row per ticker/day even if bronze was re-ingested
select ticker, trade_date, open, high, low, close, adj_close, volume
from (
    select *, row_number() over (partition by ticker, trade_date order by trade_date) as rn
    from cleaned
)
where rn = 1