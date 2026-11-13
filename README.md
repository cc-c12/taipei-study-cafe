# 台北讀書咖啡誌

用捷運站當索引 找到適合你的讀書咖啡廳或好吃好喝的店 每一間都是我去過的(還有很多要補)

- **網站**：<https://cc-c12.github.io/taipei-study-cafe/>
- **給 AI 的工作手冊**：[`AGENTS.md`](AGENTS.md)（`CLAUDE.md` 只有一行 `@AGENTS.md`，兩種工具讀的是同一份）
- **手冊的變更紀錄**：[`log.md`](log.md)

## 這個專案的重點

1. **三層架構（LLM Wiki）**：`raw/` 是我的造訪筆記（AI 只讀）→ `content/` 是 AI 依手冊寫的知識頁 → `docs/` 是自動產生的網站
2. **規則由程式執行**：`scripts/build.py` 會檢查必填欄位、選項值、站內連結、情境標籤、原始筆記是否存在(包含要來打適不適合讀書的星星跟我本人的小評)違規就建置失敗
3. **星數由公式計算**：讀書友善度只看插座、Wi-Fi、安靜、營業到晚上四件事，不能自己手打分數避免每次標準不一，所以低星不代表咖啡廳不好!!可能只是比較沒那麼適合待很久讀書做事之類的，可以點進去看我的評論

## 怎麼新增咖啡廳

1. 把造訪筆記存成 `raw/notes/YYYY-MM-DD-<slug>.md`（範本：`templates/visit-note.md`）。
2. 開一個新對話，跟 AI 說：「先讀 AGENTS.md，收錄 `raw/notes/……`」。
3. AI 會先提出計畫，你同意後它會建立頁面、更新捷運站頁，並執行建置檢查

## 自己建置

需要 Python 3.9 以上，不需要安裝任何套件。

```bash
py scripts/build.py           # 檢查規則並產生 docs/
py scripts/build.py --check   # 只檢查（健檢）
```

本機預覽：`py -m http.server 8000 -d docs`，再打開 <http://localhost:8000>。

## 資料夾

| 資料夾 | 內容 | 誰寫 |
|---|---|---|
| `raw/` | 造訪筆記、事後補充 | 我（AI 只讀） |
| `content/` | 咖啡廳頁、捷運站頁、主題清單、固定頁 | AI 依手冊撰寫 |
| `templates/` | 新頁面範本 | — |
| `scripts/` | 建置與照片處理程式 | — |
| `assets/` | 樣式、篩選程式、網頁用照片 | — |
| `process/` | 作業歷程：訪談紀錄、實測紀錄 | — |
| `docs/` | 網站成品（GitHub Pages 從這裡發布） | 自動產生，不要手改 |
