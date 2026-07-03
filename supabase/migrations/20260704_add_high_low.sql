-- 技術指標（KD）需要最高最低價（已於 2026-07-04 透過 Management API 執行）
alter table daily_closes add column if not exists high numeric;
alter table daily_closes add column if not exists low numeric;
