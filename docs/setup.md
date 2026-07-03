# 部署設定步驟

架構：LINE → FastAPI（HF Space `dadamouse/line-stock-bot`）→ Supabase ＋ TWSE API

## 1. Supabase 建表

1. 到 [Supabase Dashboard](https://supabase.com/dashboard) 建立專案（若還沒有）
2. 進入 **SQL Editor**，貼上並執行 [`supabase/schema.sql`](../supabase/schema.sql)
3. 到 **Project Settings > API** 記下：
   - `Project URL`（如 `https://xxxx.supabase.co`）
   - `service_role` key（⚠️ 是 service_role，不是 anon key）

## 2. HF Space Secrets

到 HF Space `dadamouse/line-stock-bot` 的 **Settings > Variables and secrets** 新增（全部設為 **Secret**）：

| 名稱 | 值 |
|------|-----|
| `SUPABASE_URL` | Supabase Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | service_role key |
| `LINE_CHANNEL_SECRET` | LINE channel 的 Channel secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE channel 的 Channel access token |

## 3. 部署到 HF Space

本 repo 就是 Space 的內容，直接推上去（HF Space 本身是 git repo）：

```bash
# 第一次：加 remote（密碼用 HF access token，到 hf.co/settings/tokens 建立 write 權限的 token）
git remote add space https://huggingface.co/spaces/dadamouse/line-stock-bot

# 推送（Space 原有內容會被取代）
git push --force space main
```

推送後 Space 會自動 build Docker image 並啟動。到 Space 頁面確認狀態是 **Running**，
開啟 `https://dadamouse-line-stock-bot.hf.space/` 應回傳 `{"status":"ok","service":"line-stock-bot"}`。

> 啟動時 server 會自動從 TWSE OpenAPI 同步上市公司對照表進 `stocks` 表
> （所以「新增緯創」查得到 3231），每次重啟都會更新，不需手動維護。

## 4. LINE Developers Console 設定

到 [LINE Developers Console](https://developers.line.biz/console/) 的 Messaging API channel：

1. **Webhook URL** 填入：`https://dadamouse-line-stock-bot.hf.space/webhook/line`
2. 開啟 **Use webhook**
3. 按 **Verify**（應顯示 Success）
4. 到 LINE Official Account Manager 關閉「自動回應訊息」，避免罐頭訊息干擾

## 5. 測試

用手機 LINE 加入官方帳號後依序輸入：

| 輸入 | 預期回覆 |
|------|---------|
| `登入dada` | ✅ 已登入「dada」 |
| `新增2330 1000 850` | ✅ 已為 dada 新增 台積電（2330）1,000 股＠850 |
| `新增緯創` | ✅ 已為 dada 新增 緯創（3231）（觀察，未記股數） |
| `我的股票` | 📊 持股清單，含收盤價、市值、損益 |
| `登入媽媽`（家人手機） | 建立媽媽身份 |
| `切換媽媽` | 🔁 之後操作都記在媽媽帳戶 |
| `刪除2330` | 🗑 刪除該檔全部紀錄 |
| 任意亂字 | 📖 指令說明 |

安全性驗證（可選）：用 curl 送假簽章，應回 403、沒有 LINE 回覆、資料庫不變動：

```bash
curl -s -X POST 'https://dadamouse-line-stock-bot.hf.space/webhook/line' \
  -H 'Content-Type: application/json' \
  -H 'x-line-signature: bogus' \
  -d '{"events":[{"type":"message","replyToken":"x","source":{"userId":"U1"},"message":{"type":"text","text":"登入hacker"}}]}'
```

## 開發

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests/          # 21 個單元＋整合測試
.venv/bin/uvicorn app.main:app --reload    # 本機啟動（需先 export 四個環境變數）
```

改完程式 → 測試通過 → commit → `git push --force space main` 即部署。

## 已知限制

- 支援**上市**（TWSE）與**上櫃**（TPEx）股票；ETF 不在公司對照表中，但會自動嘗試兩邊報價
- 報價是**收盤價**（最近交易日），不是即時價
- HF free Space 若休眠，第一則訊息可能因喚醒延遲超過 reply token 時效而未回覆，再傳一次即可
