-- 每日收盤快照排程（Supabase pg_cron ＋ pg_net）
-- ⚠️ 執行前把下面兩處 YOUR_CRON_SECRET 換成你設在 HF Space 的 CRON_SECRET 值
-- pg_cron 使用 UTC：06:30 UTC = 台北 14:30（收盤後）

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- 主要排程：週一到週五 14:30（台北）
select cron.schedule(
  'daily-stock-snapshot',
  '30 6 * * 1-5',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/daily-snapshot',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- 備援排程：14:40 再打一次（若 Space 剛從休眠喚醒導致第一次失敗；upsert 重跑無副作用）
select cron.schedule(
  'daily-stock-snapshot-retry',
  '40 6 * * 1-5',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/daily-snapshot',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- 晚間排程：台北 22:30（UTC 14:30）——TWSE 法人/融資融券資料傍晚後才公布，晚間再收一次當日資料
select cron.schedule(
  'daily-stock-snapshot-evening',
  '30 14 * * 1-5',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/daily-snapshot',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- 17:50（台北）收盤快照（收當日收盤價＋三大法人）
select cron.schedule(
  'daily-stock-snapshot-close',
  '50 9 * * 1-5',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/daily-snapshot',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- 每日選股＋大盤體檢推播：台北 18:00（法人 16:00 已公布；融資為前一日，隔天早上補最新）
select cron.schedule(
  'daily-stock-picks',
  '0 10 * * 1-5',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/daily-picks',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- 查看排程：select * from cron.job;
-- 查看執行紀錄：select * from cron.job_run_details order by start_time desc limit 10;
-- 取消排程：select cron.unschedule('daily-stock-snapshot');

-- 週六 09:00（台北）持股週報推播
select cron.schedule(
  'weekly-report',
  '0 1 * * 6',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/weekly-report',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- 週日 09:00（台北）下週展望推播＋回補健檢
select cron.schedule(
  'weekly-outlook',
  '0 1 * * 0',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/weekly-outlook',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- 平日 08:05（台北）盤前總經快報（美股隔夜收盤；日韓開盤 5 分鐘後抓今日初段走勢）
select cron.schedule(
  'morning-macro',
  '5 0 * * 1-5',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/morning-macro',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- 平日 08:35（台北）開盤前導航（ADR 隱含價、台股試撮、昨日台股、今日除權息）
select cron.schedule(
  'morning-open',
  '35 0 * * 1-5',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/morning-open',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- 週六 08:30（台北）同步集保股權分散（TDCC 每週五資料，千張大戶比/股東人數）
select cron.schedule(
  'weekly-holders-sync',
  '30 0 * * 6',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/sync-holders',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 30000
  );
  $$
);

-- 平日 22:50（台北）主力買賣超同步（MoneyDJ 分點頁，每檔 2 秒間隔 16 檔約 40 秒，timeout 加長）
select cron.schedule(
  'daily-broker-flows',
  '50 14 * * 1-5',
  $$
  select net.http_post(
    url := 'https://dadamouse-line-stock-bot.hf.space/admin/sync-broker-flows',
    headers := '{"x-cron-secret": "YOUR_CRON_SECRET"}'::jsonb,
    timeout_milliseconds := 90000
  );
  $$
);
