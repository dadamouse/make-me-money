-- 每日選股 SQL functions（PostgREST RPC 呼叫）

-- 資料深度：各表累積了幾個交易日（判斷策略是否可用）
create or replace function snapshot_depth()
returns table (insti_days bigint, close_days bigint)
language sql stable as $$
  select
    (select count(distinct trade_date) from daily_institutional),
    (select count(distinct trade_date) from daily_closes)
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

-- 策略三：帶量突破 20 日新高（量 > 前20日均量 1.5 倍、今日至少 1000 張）
create or replace function breakout_picks(limit_n int default 5, p_market text default null, min_volume numeric default 1000000)
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
    select r.stock_no, max(r.close) as prior_high, avg(r.volume) as prior_avg_vol, count(*) as cnt
    from ranked r where r.rn between 2 and 21
    group by r.stock_no
  )
  select t.stock_no, s.name, t.close, p.prior_high, t.volume, round(p.prior_avg_vol)
  from today t
  join prior p on p.stock_no = t.stock_no
  left join stocks s on s.stock_no = t.stock_no
  where p.cnt >= 20
    and t.close > p.prior_high
    and t.volume > 1.5 * p.prior_avg_vol
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
