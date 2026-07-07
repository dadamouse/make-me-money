-- LINE 家庭股票管理工具 — Supabase schema
-- 在 Supabase Dashboard > SQL Editor 執行本檔案

-- 家庭成員（身份）
create table if not exists members (
  id bigint generated always as identity primary key,
  name text not null unique,           -- 登入用名稱，如 'dada'、'媽媽'
  created_at timestamptz not null default now()
);

-- LINE 帳號綁定與目前操作身份
create table if not exists line_bindings (
  line_user_id text primary key,       -- LINE userId
  member_id bigint references members (id),        -- 本人身份（登入時綁定）
  acting_member_id bigint references members (id), -- 目前操作身份（切換用）
  updated_at timestamptz not null default now()
);

-- 持股：每次「新增」是一筆 lot，顯示時依 stock_no 彙總
create table if not exists holdings (
  id bigint generated always as identity primary key,
  member_id bigint not null references members (id),
  stock_no text not null,              -- 股票代號，如 '2330'
  shares numeric not null default 0,   -- 股數；0 代表純觀察
  cost_price numeric,                  -- 每股成本；null 代表未記成本
  created_at timestamptz not null default now()
);

create index if not exists idx_holdings_member on holdings (member_id);

-- 上市＋上櫃公司代號↔名稱對照（server 啟動時自動從 TWSE / TPEx OpenAPI 同步）
create table if not exists stocks (
  stock_no text primary key,
  name text not null,                  -- 公司簡稱，如 '台積電'、'緯創'
  industry text,                       -- 產業別，如 '半導體'、'金融保險'
  market text                          -- '上市' 或 '上櫃'
);

create index if not exists idx_stocks_name on stocks (name);

-- 每日收盤快照（pg_cron 觸發 /admin/daily-snapshot 寫入，見 cron.sql）
create table if not exists daily_closes (
  stock_no text not null,
  trade_date date not null,
  close numeric not null,
  volume numeric,                      -- 成交股數
  open numeric,                        -- 開盤（K 線圖用）
  high numeric,                        -- 當日最高（KD／K 線圖用）
  low numeric,                         -- 當日最低
  created_at timestamptz not null default now(),
  primary key (stock_no, trade_date)
);

create index if not exists idx_daily_closes_date on daily_closes (trade_date);

-- 每日融資融券（餘額與增減，單位：張）
create table if not exists daily_margins (
  stock_no text not null,
  trade_date date not null,
  margin_balance numeric,              -- 融資今日餘額
  margin_change numeric,               -- 融資增減（今日－前日）
  short_balance numeric,               -- 融券今日餘額
  short_change numeric,                -- 融券增減
  created_at timestamptz not null default now(),
  primary key (stock_no, trade_date)
);

create index if not exists idx_daily_margins_date on daily_margins (trade_date);

-- 三大法人每日買賣超（單位：股）
create table if not exists daily_institutional (
  stock_no text not null,
  trade_date date not null,
  foreign_net numeric,                 -- 外陸資買賣超（不含外資自營商）
  trust_net numeric,                   -- 投信買賣超
  dealer_net numeric,                  -- 自營商買賣超
  total_net numeric,                   -- 三大法人合計
  created_at timestamptz not null default now(),
  primary key (stock_no, trade_date)
);

create index if not exists idx_daily_institutional_date on daily_institutional (trade_date);

-- 除權息日（預告表每日累積）
create table if not exists dividend_events (
  stock_no text not null,
  ex_date date not null,               -- 除權息交易日
  kind text,                           -- '息'、'權'、'權息'
  cash_dividend numeric,               -- 現金股利（每股）
  stock_dividend_ratio numeric,        -- 股票股利配股率
  created_at timestamptz not null default now(),
  primary key (stock_no, ex_date)
);

create index if not exists idx_dividend_events_date on dividend_events (ex_date);

-- RLS：僅由 server 以 service_role key 存取（service_role 會繞過 RLS），
-- 若 Supabase 提示 Enable automatic RLS 可直接開啟，不影響 bot 運作。

-- 量增排行 function（內容見 migrations/20260704_volume_surge_fn.sql）
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

-- 每日選股 SQL functions（PostgREST RPC 呼叫）

-- 資料深度：各表累積了幾個交易日（判斷策略是否可用）
create or replace function snapshot_depth()
returns table (insti_days bigint, close_days bigint, margin_days bigint)
language sql stable as $$
  select
    (select count(distinct trade_date) from daily_institutional),
    (select count(distinct trade_date) from daily_closes),
    (select count(distinct trade_date) from daily_margins)
$$;

-- 策略一：法人連買 N 日（外資或投信連續買超，依合計買超排序）
create or replace function institutional_streak_picks(days int default 3, limit_n int default 5, p_market text default null)
returns table (stock_no text, stock_name text, foreign_streak boolean, trust_streak boolean, sum_net numeric)
language sql stable as $$
  with recent_dates as (
    select distinct trade_date from daily_institutional order by trade_date desc limit days
  ),
  agg as (
    select di.stock_no,
           count(*) filter (where di.foreign_net > 0) as f_days,
           count(*) filter (where di.trust_net > 0) as t_days,
           count(*) as present_days,
           sum(coalesce(di.foreign_net, 0) + coalesce(di.trust_net, 0)) as net_sum
    from daily_institutional di
    where di.trade_date in (select trade_date from recent_dates)
    group by di.stock_no
  )
  select a.stock_no, s.name, a.f_days = days, a.t_days = days, a.net_sum
  from agg a
  left join stocks s on s.stock_no = a.stock_no
  where a.present_days = days and (a.f_days = days or a.t_days = days)
    and (p_market is null or s.market = p_market)
  order by a.net_sum desc
  limit limit_n
$$;

-- 策略二：外資投信同買（最新交易日兩者皆買超）
create or replace function co_buy_picks(limit_n int default 5, p_market text default null)
returns table (stock_no text, stock_name text, foreign_net numeric, trust_net numeric)
language sql stable as $$
  with latest as (select max(trade_date) as d from daily_institutional)
  select di.stock_no, s.name, di.foreign_net, di.trust_net
  from daily_institutional di
  join latest on di.trade_date = latest.d
  left join stocks s on s.stock_no = di.stock_no
  where di.foreign_net > 0 and di.trust_net > 0
    and (p_market is null or s.market = p_market)
  order by di.foreign_net + di.trust_net desc
  limit limit_n
$$;

-- 策略三：帶量突破 20 日新高
-- 量能條件：今日量 > 前 5 日均量 × vol_multiple（上市 3 倍／上櫃 10 倍），並以 min_volume 張數當保底
create or replace function breakout_picks(
  limit_n int default 5,
  p_market text default null,
  min_volume numeric default 1000000,
  vol_multiple numeric default 3
)
returns table (stock_no text, stock_name text, close numeric, high20 numeric, volume numeric, avg_volume numeric)
language sql stable as $$
  with ranked as (
    select dc.stock_no, dc.trade_date, dc.close, dc.volume,
           row_number() over (partition by dc.stock_no order by dc.trade_date desc) as rn
    from daily_closes dc
    where dc.trade_date >= current_date - 45 and dc.volume is not null
  ),
  today as (select * from ranked where rn = 1),
  prior as (
    select r.stock_no, max(r.close) as prior_high, count(*) as cnt
    from ranked r where r.rn between 2 and 21
    group by r.stock_no
  ),
  prior5 as (
    select r.stock_no, avg(r.volume) as avg_vol5, count(*) as cnt5
    from ranked r where r.rn between 2 and 6
    group by r.stock_no
  )
  select t.stock_no, s.name, t.close, p.prior_high, t.volume, round(p5.avg_vol5)
  from today t
  join prior p on p.stock_no = t.stock_no
  join prior5 p5 on p5.stock_no = t.stock_no
  left join stocks s on s.stock_no = t.stock_no
  where p.cnt >= 20 and p5.cnt5 >= 5
    and t.close > p.prior_high
    and t.volume > vol_multiple * p5.avg_vol5
    and t.volume >= min_volume
    and (p_market is null or s.market = p_market)
  order by (t.close - p.prior_high) / p.prior_high desc
  limit limit_n
$$;

-- 策略四：KD 低檔黃金交叉（slow stochastic 近似：K=SMA3(RSV9)、D=SMA3(K)；昨日 K<30 且 K<=D，今日 K>D）
create or replace function kd_golden_cross_picks(limit_n int default 5, p_market text default null)
returns table (stock_no text, stock_name text, close numeric, k_val numeric, d_val numeric)
language sql stable as $$
  with base as (
    select dc.stock_no, dc.trade_date, dc.close, dc.high, dc.low
    from daily_closes dc
    where dc.trade_date >= current_date - 40 and dc.high is not null and dc.low is not null
  ),
  rsv as (
    select b.stock_no, b.trade_date, b.close,
           count(*) over w as wcnt,
           case when max(b.high) over w = min(b.low) over w then 50
                else (b.close - min(b.low) over w) / (max(b.high) over w - min(b.low) over w) * 100 end as rsv_val
    from base b
    window w as (partition by b.stock_no order by b.trade_date rows between 8 preceding and current row)
  ),
  k_calc as (
    select r.stock_no, r.trade_date, r.close, r.wcnt,
           avg(r.rsv_val) over w2 as kv
    from rsv r
    window w2 as (partition by r.stock_no order by r.trade_date rows between 2 preceding and current row)
  ),
  kd as (
    select k.stock_no, k.trade_date, k.close, k.wcnt, k.kv,
           avg(k.kv) over w3 as dv,
           row_number() over (partition by k.stock_no order by k.trade_date desc) as rn
    from k_calc k
    window w3 as (partition by k.stock_no order by k.trade_date rows between 2 preceding and current row)
  )
  select t.stock_no, s.name, t.close, round(t.kv::numeric, 1), round(t.dv::numeric, 1)
  from kd t
  join kd y on y.stock_no = t.stock_no and y.rn = 2
  left join stocks s on s.stock_no = t.stock_no
  where t.rn = 1 and t.wcnt >= 9
    and t.kv > t.dv
    and y.kv <= y.dv
    and y.kv < 30
    and (p_market is null or s.market = p_market)
  order by t.kv - t.dv desc
  limit limit_n
$$;

-- 策略五：融資減、價格漲（最新交易日融資減少且股價上漲，散戶籌碼退場）
create or replace function margin_reduce_price_up_picks(
  limit_n int default 5,
  p_market text default null,
  min_reduce numeric default 100
)
returns table (stock_no text, stock_name text, margin_change numeric, close numeric, prev_close numeric)
language sql stable as $$
  with ranked_m as (
    select dm.stock_no, dm.margin_change,
           row_number() over (partition by dm.stock_no order by dm.trade_date desc) as rn
    from daily_margins dm
    where dm.trade_date >= current_date - 7 and dm.margin_change is not null
  ),
  m as (select * from ranked_m where rn = 1),
  ranked_c as (
    select dc.stock_no, dc.close,
           lag(dc.close) over (partition by dc.stock_no order by dc.trade_date) as prev_close,
           row_number() over (partition by dc.stock_no order by dc.trade_date desc) as rn
    from daily_closes dc
    where dc.trade_date >= current_date - 14
  ),
  c as (select * from ranked_c where rn = 1 and prev_close is not null)
  select m.stock_no, s.name, m.margin_change, c.close, c.prev_close
  from m
  join c on c.stock_no = m.stock_no
  left join stocks s on s.stock_no = m.stock_no
  where m.margin_change <= -min_reduce
    and c.close > c.prev_close
    and (p_market is null or s.market = p_market)
  order by m.margin_change asc
  limit limit_n
$$;

-- 策略六：高券資比（融券餘額／融資餘額，軋空潛力；融資餘額需達門檻避免失真）
create or replace function short_margin_ratio_picks(
  limit_n int default 5,
  p_market text default null,
  min_margin numeric default 1000
)
returns table (stock_no text, stock_name text, short_balance numeric, margin_balance numeric, ratio numeric)
language sql stable as $$
  with ranked_m as (
    select dm.*,
           row_number() over (partition by dm.stock_no order by dm.trade_date desc) as rn
    from daily_margins dm
    where dm.trade_date >= current_date - 7
  )
  select dm.stock_no, s.name, dm.short_balance, dm.margin_balance,
         round(dm.short_balance / dm.margin_balance * 100, 1)
  from ranked_m dm
  left join stocks s on s.stock_no = dm.stock_no
  where dm.rn = 1
    and dm.margin_balance >= min_margin
    and dm.short_balance is not null and dm.short_balance > 0
    and (p_market is null or s.market = p_market)
  order by dm.short_balance / dm.margin_balance desc
  limit limit_n
$$;

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
