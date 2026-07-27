-- 雙官方帳號分流：綁定記錄來源 channel（1=原帳號、2=新免費帳號）
-- 從哪個帳號的 webhook 登入就綁哪個 channel，推播按 channel 用對應 token 發送
alter table line_bindings add column if not exists channel smallint not null default 1;
