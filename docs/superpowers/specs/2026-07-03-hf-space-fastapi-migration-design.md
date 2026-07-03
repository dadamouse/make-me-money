# 架構變更：bot 邏輯從 n8n 移到 HF Space（FastAPI）

> 本文件更新 [2026-07-03-line-stock-bot-design.md](./2026-07-03-line-stock-bot-design.md)。
> 資料模型（Supabase 四張表）、指令集、TWSE 抓價邏輯、身份機制皆不變，
> 僅執行環境從 n8n workflow 改為 HF Space 上的 FastAPI server。

## 變更原因

n8n workflow 以 JSON 匯出檔管理，程式碼審閱、版本控管、測試都不方便（使用者回饋「很難管控」）。
改放 HF Space `dadamouse/stock`（Docker Space）後，程式碼直接進 git，`git push` 即部署。

## 決策

- **語言**：Python FastAPI（使用者選擇；HF 生態最常見，未來加資料分析/圖表方便）
- **n8n 版本**：移除（git 歷史可找回），避免兩套並存 drift
- **對照表同步**：原 n8n `sync-stock-list` workflow 改為 **server 啟動時自動同步**
  （HF Space 重啟頻繁，等於免維護的定期更新；失敗時沿用資料庫既有資料，不影響服務）

## 新架構

```
家人 LINE ──▶ LINE Platform ──webhook──▶ FastAPI (dadamouse-stock.hf.space)
                                           │ POST /webhook/line（HMAC 驗簽章）
                                ┌──────────┼──────────┐
                                ▼          ▼          ▼
                            指令解析    Supabase    TWSE API
                                └── LINE reply API ──▶ 回覆訊息
```

## 模組劃分（app/）

| 檔案 | 職責 |
|------|------|
| `main.py` | FastAPI app 工廠、webhook 路由、啟動時同步對照表 |
| `config.py` | 環境變數載入與驗證（缺少即 fail fast） |
| `parser.py` | 純函式：指令解析、TWSE 回應解析、彙總與訊息格式化 |
| `handlers.py` | 指令處理（登入/切換/新增/刪除/清單） |
| `supabase.py` | PostgREST 極簡 client |
| `twse.py` | 收盤價查詢（月初 fallback）、上市公司對照表同步 |
| `line_client.py` | 簽章驗證、reply API |

`create_app(settings, transport)` 工廠支援注入 httpx MockTransport，
整合測試用記憶體版 PostgREST 模擬完整流程（21 個測試）。

## 部署

- HF Docker Space：`Dockerfile` + `README.md` frontmatter（`sdk: docker`、`app_port: 7860`）
- 本 repo 即 Space repo：`git push --force space main`
- Secrets 設在 Space Settings：`SUPABASE_URL`、`SUPABASE_SERVICE_ROLE_KEY`、
  `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`
- Webhook URL：`https://dadamouse-stock.hf.space/webhook/line`
