select distinct
    trade_date,
    year(trade_date)         as year,
    month(trade_date)        as month,
    day_of_week(trade_date)  as day_of_week
from {{ ref('stg_prices') }}