-- 全市場融資維持率（設計：docs/superpowers/specs/2026-08-06-margin-maintenance-macro-design.md）
-- 維持率 = 上市融資擔保品市值（每檔餘額×收盤價）÷ 上市融資餘額金額 × 100

create table if not exists market_margin (
  trade_date date primary key,
  financing_amount numeric,   -- 上市融資餘額金額（仟元，TWSE MI_MARGN 彙總）
  collateral_value numeric,   -- 上市融資擔保品市值（仟元，自家餘額×收盤價）
  maintenance_pct numeric,
  created_at timestamptz not null default now()
);

create or replace function margin_collateral_value(p_trade_date date)
returns table (collateral_value numeric)
language sql stable as $$
  select sum(dm.margin_balance * 1000 * dc.close) / 1000
  from daily_margins dm
  join daily_closes dc on dc.stock_no = dm.stock_no and dc.trade_date = dm.trade_date
  join stocks s on s.stock_no = dm.stock_no
  where dm.trade_date = p_trade_date
    and s.market = '上市'
    and dm.margin_balance is not null
    and dc.close is not null
$$;
