-- 大盤日資料（加權指數＋成交金額，來源 TWSE FMTQIK）
create table if not exists daily_market (
  trade_date date primary key,
  taiex numeric not null,              -- 發行量加權股價指數收盤
  amount numeric,                      -- 市場成交金額（元）
  created_at timestamptz not null default now()
);

-- 大盤指數序列（由新到舊）
create or replace function market_series(n int default 60)
returns table (trade_date date, taiex numeric, amount numeric)
language sql stable as $$
  select dm.trade_date, dm.taiex, dm.amount
  from daily_market dm
  order by dm.trade_date desc
  limit n
$$;

-- 全市場法人/融資日彙總序列（由新到舊）
create or replace function market_flow_series(n int default 10)
returns table (trade_date date, insti_net numeric, margin_chg numeric)
language sql stable as $$
  with i as (select di.trade_date, sum(di.total_net) as net from daily_institutional di group by di.trade_date),
       m as (select dm.trade_date, sum(dm.margin_change) as chg from daily_margins dm group by dm.trade_date)
  select coalesce(i.trade_date, m.trade_date), i.net, m.chg
  from i full join m on m.trade_date = i.trade_date
  order by 1 desc
  limit n
$$;

-- 市場寬度：今日漲跌家數、創 20 日新高/新低家數
create or replace function market_breadth()
returns table (up_count bigint, down_count bigint, new_high bigint, new_low bigint)
language sql stable as $$
  with ranked as (
    select dc.stock_no, dc.close,
           row_number() over (partition by dc.stock_no order by dc.trade_date desc) as rn
    from daily_closes dc
    join stocks s on s.stock_no = dc.stock_no  -- 限正規對照表，排除權證/債券等
    where dc.trade_date >= current_date - 40
  ),
  today as (select * from ranked where rn = 1),
  prev as (select * from ranked where rn = 2),
  range20 as (
    select r.stock_no, max(r.close) as hi, min(r.close) as lo
    from ranked r where r.rn between 2 and 21
    group by r.stock_no
    having count(*) >= 20
  )
  select
    count(*) filter (where t.close > p.close),
    count(*) filter (where t.close < p.close),
    count(*) filter (where g.hi is not null and t.close > g.hi),
    count(*) filter (where g.lo is not null and t.close < g.lo)
  from today t
  join prev p on p.stock_no = t.stock_no
  left join range20 g on g.stock_no = t.stock_no
$$;
