# 部署設定步驟

架構：LINE → n8n（dadamouse-n8n-free.hf.space）→ Supabase ＋ TWSE API

## 1. Supabase 建表

1. 到 [Supabase Dashboard](https://supabase.com/dashboard) 建立專案（若還沒有）
2. 進入 **SQL Editor**，貼上並執行 [`supabase/schema.sql`](../supabase/schema.sql)
3. 到 **Project Settings > API** 記下：
   - `Project URL`（如 `https://xxxx.supabase.co`）
   - `service_role` key（⚠️ 是 service_role，不是 anon key）

## 2. HF Space 環境變數

到 n8n 的 HF Space（`dadamouse-n8n-free`）**Settings > Variables and secrets** 新增：

| 名稱 | 值 | 類型 |
|------|-----|------|
| `SUPABASE_URL` | Supabase Project URL | Variable |
| `SUPABASE_SERVICE_ROLE_KEY` | service_role key | **Secret** |
| `LINE_CHANNEL_SECRET` | LINE channel 的 Channel secret | **Secret** |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE channel 的 Channel access token | **Secret** |
| `NODE_FUNCTION_ALLOW_BUILTIN` | `crypto` | Variable |

> `NODE_FUNCTION_ALLOW_BUILTIN=crypto` 讓 n8n Code node 可以 `require('crypto')` 做 LINE 簽章驗證。
> 儲存後 Space 會重啟，等 n8n 恢復再繼續。

## 3. 匯入 n8n workflows

1. 開啟 n8n → **Workflows > Import from File**
2. 匯入 [`n8n/sync-stock-list.json`](../n8n/sync-stock-list.json)，手動執行一次
   - 成功會輸出 `{ imported: 1000+ }`，Supabase `stocks` 表會有全部上市公司
   - 之後每月手動跑一次即可（更新新上市公司）
3. 匯入 [`n8n/line-stock-bot.json`](../n8n/line-stock-bot.json)，**啟用（Activate）** workflow
   - Production webhook URL 為：`https://dadamouse-n8n-free.hf.space/webhook/line-stock`

## 4. LINE Developers Console 設定

到 [LINE Developers Console](https://developers.line.biz/console/) 的 Messaging API channel：

1. **Webhook URL** 填入：`https://dadamouse-n8n-free.hf.space/webhook/line-stock`
2. 開啟 **Use webhook**
3. 按 **Verify**（n8n workflow 必須已 Activate 才會回 200）
4. 到 LINE Official Account Manager 關閉「自動回應訊息」，避免罐頭訊息干擾

## 5. 測試

用手機 LINE 加入官方帳號後依序輸入：

| 輸入 | 預期回覆 |
|------|---------|
| `登入dada` | ✅ 已登入「dada」 |
| `新增2330 1000 850` | ✅ 已為 dada 新增 台積電（2330）1000 股＠850 |
| `新增緯創` | ✅ 已為 dada 新增 緯創（3231）（觀察，未記股數） |
| `我的股票` | 📊 持股清單，含收盤價、市值、損益 |
| `登入媽媽`（家人手機） | 建立媽媽身份 |
| `切換媽媽` | 🔁 之後操作都記在媽媽帳戶 |
| `刪除2330` | 🗑 刪除該檔全部紀錄 |
| 任意亂字 | 📖 指令說明 |

安全性驗證（可選）：用 curl 送假簽章，應該不會有任何 LINE 回覆、資料庫也不會變動：

```bash
curl -s -X POST 'https://dadamouse-n8n-free.hf.space/webhook/line-stock' \
  -H 'Content-Type: application/json' \
  -H 'x-line-signature: bogus' \
  -d '{"events":[{"type":"message","replyToken":"x","source":{"userId":"U1"},"message":{"type":"text","text":"登入hacker"}}]}'
```

## 開發

- 修改邏輯：編輯 `n8n/src/*.js` → `npm test` → `npm run build` → 重新匯入 JSON 到 n8n
- 單元＋整合測試：`npm test`（不需安裝任何依賴，node 18+ 內建 test runner）

## 已知限制

- 只支援**上市**股票（TWSE）；上櫃（TPEx）查不到報價
- 報價是**收盤價**（最近交易日），不是即時價
- HF free Space 若休眠，第一則訊息可能因喚醒延遲而未回覆（reply token 30 秒過期），再傳一次即可
