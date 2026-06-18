# 小红书监控工程 - 架构总览 (ARCHITECTURE)

## 1. 关键节点流程图

![architecture](./architecture.svg)

## 2. 五个关键节点

### ① 任务拆解（Task Decomposition）
- **输入**：用户自然语言需求（如"监控 AI Agent 工程师招聘"）
- **输出**：`tasks.json` 中的关键词列表
- **执行者**：用户（通过飞书 Bot 命令）或 LLM 自动拆解

### ② 小红书搜索（XHS Search）
- **方案**：Playwright + 系统 Chrome（Linux 下用 `/snap/bin/chromium`）
- **关键点**：
  - 搜索结果链接格式：`/search_result/{noteId}?xsec_token=xxx`
  - 必须带 `xsec_token` 才能访问详情页
  - DOM 选择器：`section.note-item > a.cover.ld`（小红书改版需调整）
- **输出**：noteId 列表 + 搜索结果摘要

### ③ 增量去重（Deduplication）
- **存储**：SQLite `db/monitor.db`
- **去重维度**：
  - **noteId 主键**：同一个帖子只入库一次
  - **content_hash**：防搬运帖（不同 noteId 相同内容）
- **状态机**：`new` → `pushed` / `filtered` / `dup_content`

### ④ 内容解析（Content Parsing）
- **方案**：Playwright 中用 `page.evaluate` 解析 `window.__INITIAL_STATE__`
- **优势**：
  - 不依赖 requests（被反爬拦截）
  - 不依赖正则（会匹配到页面底部备案信息）
  - 直接定位 `noteDetailMap[noteId]` 节点
- **提取字段**：title / desc / imageList / video / user.nickname / interactInfo

### ⑤ 推送（Delivery）
- **通道**：飞书 Bot P2P 私聊
- **格式**：Markdown 简报（每条含标题/作者/点赞/摘要/标签/链接）
- **触发条件**：有新增帖子才推，无新增静默
- **API**：`lark-cli im +messages-send --as bot`

---

## 3. 数据流向

```
┌──────────────────────────────────────────────────────────────────┐
│                         飞书 (Feishu)                              │
│                                                                  │
│  用户发命令 ──────────────────────► 飞书 Bot 监听                 │
│  /list /add /delete /status /run  ◄──── lark-cli event consume   │
└─────────────────┬────────────────────────────────────────────────┘
                  │ 命令
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                      xhs_monitor/bot.py                          │
│                                                                  │
│   - 解析命令                                                     │
│   - 更新 tasks.json                                              │
│   - 异步启动 pipeline                                            │
└─────────────────┬────────────────────────────────────────────────┘
                  │ 触发
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                    xhs_monitor/pipeline.py                       │
│                                                                  │
│   ② Playwright 搜索 ──► ③ SQLite 去重 ──► ④ SSR 解析            │
└─────────────────┬────────────────────────────────────────────────┘
                  │ 新增帖子 JSON
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                    xhs_monitor/push_feishu.py                    │
│                                                                  │
│   - 读取 latest_result.json                                      │
│   - 渲染 Markdown                                                │
│   - 调用 lark-cli im +messages-send                              │
└─────────────────┬────────────────────────────────────────────────┘
                  │ 飞书消息
                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                  飞书 P2P 私聊 (用户收到)                         │
└──────────────────────────────────────────────────────────────────┘
```

## 4. 组件清单

| 文件 | 作用 | 行数 |
|------|------|------|
| `config.py` | 全局配置（路径/选择器/浏览器/header） | 50 |
| `searcher.py` | Playwright 搜索 + 详情解析 | 230 |
| `content_parser.py` | SSR 数据 → 结构化字段 | 70 |
| `dedup.py` | SQLite 双层去重 | 90 |
| `pipeline.py` | 主流程编排 | 175 |
| `push_feishu.py` | 飞书推送 | 185 |
| `bot.py` | 飞书 Bot 命令处理 | 360 |
| `manage.py` | CLI 任务管理（add/remove/list/run/status） | 380 |
| `save_cookies.py` | 手动登录保存 Cookie | 60 |

## 5. 部署架构

```
┌─────────────────────────────────────────────────────────────┐
│                    远程服务器 dev@192.168.39.240              │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ systemd --user (linger=enabled)                     │    │
│  │                                                     │    │
│  │  xhs-monitor-bot.service        持续运行            │    │
│  │   └─ lark-cli event consume    监听飞书消息          │    │
│  │                                                     │    │
│  │  xhs-monitor-pipeline.timer    每6小时触发          │    │
│  │   └─ xhs-monitor-pipeline.service                    │    │
│  │       └─ python3 manage.py run                       │    │
│  │           ├─ pipeline.py  (Playwright + Chromium)   │    │
│  │           └─ push_feishu.py                         │    │
│  │                                                     │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  数据: tasks.json, db/monitor.db, cookies/, output/         │
└─────────────────────────────────────────────────────────────┘
```

## 6. 关键技术决策

| 决策 | 原因 |
|------|------|
| Playwright（非接口逆向） | 签名频繁更新，维护成本低 |
| 复用系统 Chrome | 跳过 Chromium 下载（CDN 被墙） |
| SSR 对象解析（非正则 HTML） | 精确提取，避免匹配备案信息 |
| SQLite（不用 Redis） | 单机部署无并发，去重查询足够 |
| lark-cli（不用 SDK） | CLI 跨平台，配置简单 |
| systemd --user | 比 nohup/crontab 更规范，支持自动重启 |

## 7. 故障恢复链路

| 故障 | 表现 | 恢复方式 |
|------|------|---------|
| Bot 进程崩溃 | lark-cli 退出码非0 | systemd Restart=always，5秒后自动重启 |
| Pipeline 失败 | service 进入 failed | `systemctl --user reset-failed` + `start` |
| 网络抖动 | Playwright timeout 30s | pipeline 内置 600s 超时，可重跑 |
| Cookie 过期 | 搜索结果为空 | 重新运行 `save_cookies.py` |
| lark-cli 认证失效 | token_missing | 重新运行 `lark-cli config init` |
| 主机重启 | 服务停止 | `loginctl enable-linger` + daemon 模式自启 |

---

*最后更新：2026-06-18*
