select
    p.ticker,
    coalesce(m.sector, 'UNMAPPED') as sector
from (select distinct ticker from {{ ref('stg_prices') }}) as p
left join {{ ref('ticker_sector_map') }} as m on p.ticker = m.ticker
