-- 每日收盤快照表（由 /admin/daily-snapshot 端點寫入）
create table if not exists daily_closes (
  stock_no text not null,
  trade_date date not null,
  close numeric not null,
  created_at timestamptz not null default now(),
  primary key (stock_no, trade_date)
);

create index if not exists idx_daily_closes_date on daily_closes (trade_date);
