-- 集保股權分散（TDCC 週資料）：股東總人數與千張大戶持股比
create table if not exists weekly_holders (
  stock_no text not null,
  week_date date not null,
  total_holders numeric,               -- 股東總人數（級距17）
  big_holders numeric,                 -- 千張大戶人數（級距15）
  big_ratio numeric,                   -- 千張大戶持股比%（級距15）
  created_at timestamptz not null default now(),
  primary key (stock_no, week_date)
);

-- 集保資料累積週數（選股策略啟用判斷）
create or replace function holders_depth()
returns table (weeks bigint)
language sql stable as $$
  select count(distinct week_date) from weekly_holders
$$;

-- 策略：籌碼集中（千張大戶比週增 >= min_ratio_gain pp 且股東人數下降；排除 ETF）
create or replace function concentration_picks(
  limit_n int default 10,
  p_market text default null,
  min_ratio_gain numeric default 0.3
)
returns table (stock_no text, stock_name text, big_ratio numeric, ratio_change numeric, holders_change_pct numeric)
language sql stable as $$
  with ranked as (
    select wh.stock_no, wh.week_date, wh.total_holders, wh.big_ratio,
           row_number() over (partition by wh.stock_no order by wh.week_date desc) as rn
    from weekly_holders wh
  ),
  cur as (select * from ranked where rn = 1),
  prev as (select * from ranked where rn = 2)
  select c.stock_no, s.name, c.big_ratio,
         round(c.big_ratio - p.big_ratio, 2),
         round((c.total_holders - p.total_holders) / nullif(p.total_holders, 0) * 100, 2)
  from cur c
  join prev p on p.stock_no = c.stock_no
  join stocks s on s.stock_no = c.stock_no
  where coalesce(s.industry, '') <> 'ETF'
    and c.big_ratio - p.big_ratio >= min_ratio_gain
    and c.total_holders < p.total_holders
    and (p_market is null or s.market = p_market)
  order by c.big_ratio - p.big_ratio desc
  limit limit_n
$$;
