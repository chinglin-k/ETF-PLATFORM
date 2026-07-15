# etf-platform
台灣 ETF 量化決策平台 (Taiwan ETF Quant Dashboard)

這是一個專為台灣 ETF 市場設計的量化分析與視覺化前端應用程式。本專案將常見的量化研究流程整合至單一 Web 介面中，協助使用者快速建立投資假設、執行資產配置演算法並進行策略回測。專案採用純前端技術打造，並內建本地模擬資料引擎以克服瀏覽器跨域（CORS）限制。

核心功能特色

🔍 嚴謹的量化篩選器：自動計算選定標的的年化報酬率、年化波動率與夏普比率（Sharpe Ratio），快速定位高風險報酬比的標的。

⚖️ 資產配置與回測引擎：實作 Markowitz 效率前緣概念，支援平均權重與最大夏普比率配置。提供深度的風險貢獻（Risk Parity）圓餅圖與資產相關性熱力圖分析。

🌍 市場情報儀表板：整合大盤基準（^TWII）的多空位階監控，包含 K 線走勢、長短天期均線與 RSI(14) 情緒指標。

⚙️ 混合式數據架構：自動串接台灣證交所 OpenAPI 獲取即時 ETF 清單，並在底層實作具備統計特性與相關性的本地模擬價格引擎，確保在無後端伺服器支援下仍能完整呈現量化分析方法論。

技術標籤 (Tech Stack)
HTML5 · CSS3 (Variables/Themes) · Vanilla JavaScript · Plotly.js · Quantitative Analysis
