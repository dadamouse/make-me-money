-- 既有資料庫升級：收盤表加成交量、新增融資融券與除權息表
alter table daily_closes add column if not exists volume numeric;

create table if not exists daily_margins (
  stock_no text not null,
  trade_date date not null,
  margin_balance numeric,
  margin_change numeric,
  short_balance numeric,
  short_change numeric,
  created_at timestamptz not null default now(),
  primary key (stock_no, trade_date)
);

create index if not exists idx_daily_margins_date on daily_margins (trade_date);

create table if not exists dividend_events (
  stock_no text not null,
  ex_date date not null,
  kind text,
  cash_dividend numeric,
  stock_dividend_ratio numeric,
  created_at timestamptz not null default now(),
  primary key (stock_no, ex_date)
);

create index if not exists idx_dividend_events_date on dividend_events (ex_date);
