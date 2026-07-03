# LINE 家庭股票管理工具 — MVP 設計計畫

## Context（背景與缺口分析）

目標：做一個透過 LINE 對話管理家人股票的工具。輸入「登入dada」進入 dada 身份，「新增2330」「新增緯創」記錄持股，「我的股票」列出持股並抓 TWSE 收盤價計算損益。

### 已有的基礎設施
| 元件 | 狀態 |
|------|------|
| LINE OA + Messaging API channel | ✅ 已有（Channel Secret / Access Token 已取得，webhook 未串） |
| n8n（https://dadamouse-n8n-free.hf.space/） | ✅ 運行中，作為 bot 邏輯主體 |
| Supabase | ✅ 有組織帳號，但尚未建資料表 |
| HF Space `dadamouse/stock` | ✅ 運行中，**MVP 用不到**（保留給未來網頁 dashboard） |

### 還缺的系統（本計畫要補的）
1. **Supabase 資料表**：成員、LINE 綁定、持股、股票代號對照表
2. **n8n LINE webhook workflow**：接收訊息、驗簽章、解析指令、回覆
3. **指令解析**：登入／切換／新增／刪除／查詢
4. **股票名稱→代號對照**（「緯創」→ 3231）：TWSE OpenAPI 匯入
5. **TWSE 報價抓取邏輯**：處理民國年日期、月初／假日無資料的 fallback

### 已確認的決策
- Bot 邏輯全部放 n8n workflow（不寫 server code）
- 記錄「股數＋成本」，顯示時計算市值與損益
- 身份機制：首次「登入dada」綁定 LINE userId；之後可用「切換媽媽」代操作家人帳戶

## 架構

```
家人 LINE ──訊息──▶ LINE Platform ──webhook──▶ n8n (dadamouse-n8n-free.hf.space)
                                                │
                                    ┌───────────┼────────────┐
                                    ▼           ▼            ▼
                              驗簽章+解析指令  Supabase    TWSE API
                                    │        (持股/身份)  (收盤價/代號表)
                                    └──── reply API ──▶ LINE 回覆訊息
```

## 資料模型（Supabase SQL）

```sql
-- 家庭成員（身份）
create table members (
  id bigint generated always as identity primary key,
  name text not null unique,          -- 'dada'、'媽媽' 等登入用名稱
  created_at timestamptz not null default now()
);

-- LINE 帳號綁定與目前操作身份
create table line_bindings (
  line_user_id text primary key,      -- LINE userId
  member_id bigint references members(id),        -- 本人身份（登入時綁定）
  acting_member_id bigint references members(id), -- 目前操作身份（切換用）
  updated_at timestamptz not null default now()
);

-- 持股（每次「新增」是一筆 lot，顯示時彙總）
create table holdings (
  id bigint generated always as identity primary key,
  member_id bigint not null references members(id),
  stock_no text not null,             -- '2330'
  shares numeric not null default 0,  -- 股數，可省略時預設 0（純觀察）
  cost_price numeric,                 -- 每股成本，可為 null
  created_at timestamptz not null default now()
);

-- 上市股票代號↔名稱對照
create table stocks (
  stock_no text primary key,
  name text not null                  -- 公司簡稱，如 '台積電'、'緯創'
);
create index idx_stocks_name on stocks(name);
```

RLS 先不開（只有 n8n 用 service_role key 存取，不對外開 API）。

## 指令集（MVP）

| 使用者輸入 | 行為 |
|-----------|------|
| `登入dada` / `登入 dada` | member 不存在則建立；綁定此 LINE userId，acting = 本人 |
| `切換媽媽` | acting_member 改為「媽媽」（需已存在），回覆確認 |
| `新增2330` / `新增 緯創` | 解析代號或名稱→代號，新增一筆 holding（shares=0） |
| `新增2330 1000 850` | 新增 1000 股、每股成本 850 |
| `刪除2330` | 刪除 acting member 該股所有 lot |
| `我的股票` / `清單` | 列出 acting member 持股：代號 名稱 收盤價 股數 市值 損益 |
| 其他 | 回覆指令說明（help） |

回覆格式範例（text message）：
```
📊 dada 的持股（收盤價 07/03）
2330 台積電 2,355.0 ×1,000
  市值 2,355,000｜損益 +1,505,000 (+177%)
3231 緯創 ...
─────
總市值 …｜總損益 …
```

## n8n Workflow 設計

### Workflow 1：`line-stock-bot`（主流程）
1. **Webhook node**（POST，path 如 `/line-stock`）— 接收 LINE events
2. **Code node：驗簽章** — 用 Channel Secret 做 HMAC-SHA256 比對 `x-line-signature`，不符則丟棄（HF Space 是公開 URL，必須驗）
3. **Code node：解析指令** — 正規表達式解析上表指令，輸出 `{action, args, lineUserId, replyToken}`；只處理 `message.text` event
4. **Switch node** — 按 action 分流
5. **各分支**：
   - Supabase 讀寫：用 n8n 的 **Supabase node**（或 HTTP Request 打 PostgREST），credential 存 service_role key
   - 名稱→代號：先查 `stocks` where `stock_no = 輸入` 或 `name = 輸入`
   - 查詢分支：對每檔持股呼叫 TWSE（見下）
6. **HTTP Request node：LINE Reply** — POST `https://api.line.me/v2/bot/message/reply`，header 帶 Channel Access Token（存 n8n credential，不寫死）

### Workflow 2：`sync-stock-list`（代號對照表，手動/每月執行）
- HTTP Request 抓 TWSE OpenAPI 上市公司基本資料 `https://openapi.twse.com.tw/v1/opendata/t187ap03_L`（含公司代號、公司簡稱）
- Upsert 進 `stocks` 表

### TWSE 報價抓取（查詢分支內）
- API：`https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=YYYYMMDD&stockNo=2330`
- `date` 用**當月任一天**即可，回傳整月的每日資料；取 `data` 陣列**最後一筆**的收盤價（index 6）
- 注意：日期為民國年（`115/06/01`）、數字含千分位逗號，要清洗
- **Fallback**：月初尚無交易日或 `stat != "OK"` 時，改查上個月
- 限制（寫進 help/文件）：只支援**上市**股票；上櫃（TPEx）不在 MVP 範圍

## 交付物（進 git repo `make-me-money`）

```
make-me-money/
├── supabase/schema.sql          # 上述建表 SQL（在 Supabase SQL Editor 執行）
├── n8n/line-stock-bot.json      # 主 workflow 匯出檔（可 import）
├── n8n/sync-stock-list.json     # 代號表同步 workflow 匯出檔
└── docs/setup.md                # 設定步驟：Supabase 建表、n8n import、
                                 # credentials 設定、LINE webhook URL 設定、測試指令
```

## 實作步驟

1. 在 Supabase 建立專案（若還沒有）＋執行 `schema.sql` 建四張表
2. 撰寫並 import `sync-stock-list` workflow，執行一次填入 `stocks` 對照表（驗證「緯創」查得到 3231）
3. 撰寫並 import `line-stock-bot` workflow：
   - 設定 n8n credentials：Supabase URL + service_role key、LINE Channel Secret、Channel Access Token
   - 啟用 workflow 取得 production webhook URL
4. 到 LINE Developers Console 把 webhook URL 設為 n8n 的 URL，開啟「Use webhook」、關閉自動回覆
5. 端到端測試（見下）
6. 把 workflow 匯出檔與 SQL commit 進 repo

## 驗證方式

1. **簽章驗證**：用 curl 送假 payload（無效簽章）→ 應被拒絕
2. **LINE 實測**（用自己手機依序輸入）：
   - `登入dada` → 回覆綁定成功
   - `新增2330 1000 850` → 回覆已新增 台積電
   - `新增緯創` → 名稱解析成功，回覆已新增 緯創(3231)
   - `我的股票` → 顯示兩檔、收盤價正確（與 TWSE 網站比對）、市值/損益計算正確
   - `切換媽媽`（先 `登入媽媽` 建身份）→ 新增/查詢作用在媽媽帳戶
   - `刪除2330` → 再查清單確認消失
   - 輸入亂字 → 回覆指令說明
3. **Supabase 後台**檢查資料列正確

## 未來擴充（不在 MVP）
- 上櫃股票支援（TPEx API）
- 即時報價（mis.twse.com.tw getStockInfo）
- HF Space `stock` 做網頁 dashboard（讀同一個 Supabase）
- 到價提醒（n8n Schedule + LINE push）
