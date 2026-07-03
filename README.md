---
title: Stock
emoji: 📈
colorFrom: green
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# make-me-money — LINE 家庭股票管理工具

透過 LINE 對話管理家人股票：登入身份、記錄持股（股數＋成本）、查詢 TWSE 收盤價與損益。

架構：LINE Messaging API → FastAPI（本 HF Space）→ Supabase ＋ TWSE API

- 部署與設定步驟：[docs/setup.md](docs/setup.md)
- 設計文件：[docs/superpowers/specs/](docs/superpowers/specs/)
- 測試：`python -m pytest tests/`

## 指令

| 輸入 | 行為 |
|------|------|
| `登入dada` | 建立/綁定身份 |
| `切換媽媽` | 代操作家人帳戶 |
| `新增2330`、`新增緯創` | 加入觀察（可用代號或公司簡稱） |
| `新增2330 1000 850` | 記 1000 股、每股成本 850 |
| `刪除2330` | 刪除該檔所有紀錄 |
| `我的股票` | 列出持股、收盤價、市值與損益 |
