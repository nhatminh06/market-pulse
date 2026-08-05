{{
    config(properties={"partitioning": "ARRAY['ticker']", "format": "'PARQUET'"})
}}

with src as (
    select * from {{ source('bronze', 'prices') }}
),

cleaned as (
    select
        ticker,
        cast(date as date) as trade_date,
        cast(open as double) as open,
        cast(high as double) as high,
        cast(low as double) as low,
        cast(close as double) as close,
        cast(adj_close as double) as adj_close,
        cast(volume as bigint) as volume,
        _ingested_at
    from src
    where
        close is not null
        and close > 0
        and volume >= 0
        and low <= high
        and (open is null or (open between low and high))
        and (close between low and high)
)

-- dedupe: keep the latest-ingested row per ticker/day if bronze was re-ingested
select
    ticker,
    trade_date,
    open,
    high,
    low,
    close,
    adj_close,
    volume
from (
    select
        *,
        row_number()
            over (partition by ticker, trade_date order by _ingested_at desc)
            as rn
    from cleaned
) as deduped
where rn = 1
