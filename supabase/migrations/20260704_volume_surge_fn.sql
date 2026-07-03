-- 量增排行：每檔股票最新交易日成交量相對前一交易日的倍數，由大到小
-- 呼叫方式（PostgREST RPC）：POST /rest/v1/rpc/volume_surge_ranking {"min_volume":1000000,"limit_n":10}
create or replace function volume_surge_ranking(min_volume numeric default 1000000, limit_n int default 10)
returns table (
  stock_no text,
  trade_date date,
  close numeric,
  prev_close numeric,
  volume numeric,
  prev_volume numeric
)
language sql stable as $$
  with recent as (
    select
      dc.stock_no,
      dc.trade_date,
      dc.close,
      dc.volume,
      lag(dc.close) over (partition by dc.stock_no order by dc.trade_date) as prev_close,
      lag(dc.volume) over (partition by dc.stock_no order by dc.trade_date) as prev_volume,
      row_number() over (partition by dc.stock_no order by dc.trade_date desc) as rn
    from daily_closes dc
    where dc.trade_date >= current_date - 14
  )
  select r.stock_no, r.trade_date, r.close, r.prev_close, r.volume, r.prev_volume
  from recent r
  where r.rn = 1
    and r.prev_volume is not null and r.prev_volume > 0
    and r.volume is not null and r.volume >= min_volume
    and r.volume > r.prev_volume
  order by r.volume / r.prev_volume desc
  limit limit_n
$$;
