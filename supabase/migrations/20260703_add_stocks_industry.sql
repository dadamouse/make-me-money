-- 已建過表的資料庫請執行本檔案（新資料庫直接跑 schema.sql 即可，不用跑這份）
alter table stocks add column if not exists industry text;
