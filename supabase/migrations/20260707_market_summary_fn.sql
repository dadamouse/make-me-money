-- 大盤日彙總：最新一日全市場法人合計與融資增減（盤前導航用）
create or replace function market_daily_summary()
returns table (insti_date date, institutional_net numeric, margin_date date, margin_change numeric)
language sql stable as $$
  with latest_i as (select max(trade_date) as d from daily_institutional),
       latest_m as (select max(trade_date) as d from daily_margins)
  select
    (select d from latest_i),
    (select sum(total_net) from daily_institutional where trade_date = (select d from latest_i)),
    (select d from latest_m),
    (select sum(margin_change) from daily_margins where trade_date = (select d from latest_m))
$$;
