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
