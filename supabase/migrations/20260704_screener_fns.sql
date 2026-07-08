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

-- 策略七：5 日強勢股（近 5 個交易日累計漲幅 >= min_gain%，今日成交量達門檻）
create or replace function momentum_picks(
  limit_n int default 5,
  p_market text default null,
  min_gain numeric default 15,
  min_volume numeric default 500000
)
returns table (stock_no text, stock_name text, close numeric, base_close numeric, gain_pct numeric)
language sql stable as $$
  with ranked as (
    select dc.stock_no, dc.close, dc.volume,
           row_number() over (partition by dc.stock_no order by dc.trade_date desc) as rn
    from daily_closes dc
    join stocks s on s.stock_no = dc.stock_no  -- 限正規對照表
    where dc.trade_date >= current_date - 14
  ),
  today as (select * from ranked where rn = 1),
  base as (select * from ranked where rn = 6)  -- 5 個交易日前收盤
  select t.stock_no, s.name, t.close, b.close,
         round((t.close - b.close) / b.close * 100, 1)
  from today t
  join base b on b.stock_no = t.stock_no
  left join stocks s on s.stock_no = t.stock_no
  where b.close > 0
    and (t.close - b.close) / b.close * 100 >= min_gain
    and t.volume is not null and t.volume >= min_volume
    and (p_market is null or s.market = p_market)
  order by (t.close - b.close) / b.close desc
  limit limit_n
$$;
