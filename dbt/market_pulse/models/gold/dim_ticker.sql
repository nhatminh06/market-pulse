select distinct
    ticker,
    case
        when ticker in ('AAPL','MSFT','NVDA','GOOGL','META') then 'Technology'
        when ticker in ('JPM','BAC','V','MA')                then 'Financials'
        when ticker in ('XOM')                               then 'Energy'
        when ticker in ('JNJ','PG','KO','PEP','WMT','COST','HD','DIS','AMZN','TSLA') then 'Consumer'
        else 'Other'
    end as sector
from {{ ref('stg_prices') }}