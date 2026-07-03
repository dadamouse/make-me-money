-- 三大法人每日買賣超（單位：股；由每日快照寫入）
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
