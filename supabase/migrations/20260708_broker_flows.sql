-- 主力買賣超（券商分點前15大彙總，來源 MoneyDJ zco 公開頁，僅同步持股）
create table if not exists daily_broker_flows (
  stock_no text not null,
  trade_date date not null,
  top_buy_lots numeric,                -- 前15大買超合計（張）
  top_sell_lots numeric,               -- 前15大賣超合計（張）
  net_lots numeric,                    -- 主力買賣超（張）
  concentration_pct numeric,           -- 集中度%（買超比重合計－賣超比重合計）
  created_at timestamptz not null default now(),
  primary key (stock_no, trade_date)
);
