# 小红书监控工程

LLM 驱动的小红书帖子监控系统。用户用自然语言描述需求 → LLM 拆解为关键词 + 过滤条件 → 飞书 Bot 管理任务组 → Playwright 定期搜索 → SQLite 去重 → 内容解析 → LLM 二次过滤 → 飞书推送。

支持两条路径：
- **关键词路径**（无 LLM 过滤）：手动添加关键词直接搜索
- **任务组路径**（LLM 拆解 + 智能过滤）：自然语言需求 → LLM 拆解 → 搜索后 LLM 二次过滤

---

## 项目结构

```
xhs_monitor/
├── bot.py                 # 飞书 Bot（命令处理 + 事件监听）
├── config.py              # 全局配置（路径/选择器/浏览器/LLM 端点）
├── content_parser.py      # SSR 数据 → 结构化字段
├── dedup.py               # SQLite 双层去重
├── llm.py                 # LLM 能力封装（decompose_task + filter_posts）
├── manage.py              # CLI 任务管理（list/add/remove/run/status/task）
├── pipeline.py            # 主流程编排（关键词路径 + 任务组路径）
├── push_feishu.py         # 飞书推送（Markdown 渲染 + LLM 匹配理由）
├── save_cookies.py        # 小红书 Cookie 获取
├── tasks.json             # 监控配置（keywords[] + tasks[] 双轨）
├── .env                   # LLM API key（不提交）
├── cookies/               # 登录态
├── db/                    # SQLite 去重库
├── output/                # 抓取结果
└── docs/
    ├── OPS.md             # 运维手册（部署/排错/备份）
    ├── ARCHITECTURE.md    # 架构总览
    ├── architecture.svg   # 关键节点流程图
    └── deployment.svg     # 部署架构图
```

## 飞书 Bot 命令

在飞书私聊 bot 发送：

### 关键词（无 LLM 过滤）
| 命令 | 作用 |
|------|------|
| `/list` | 查看所有关键词 + 任务组（统一编号） |
| `/add 关键词1 关键词2` | 添加监控关键词 |
| `/delete 1` 或 `/delete 关键词` | 删除关键词 |

### 任务组（LLM 拆解 + 智能过滤）
| 命令 | 作用 |
|------|------|
| `/task 自然语言描述` | LLM 拆解（自动生成名称 + 关键词 + 过滤条件） |
| `/task 名称 \| 描述` | LLM 拆解（指定名称） |
| `/tasks` | 查看所有任务组详情 |
| `/task-remove 名称` | 删除任务组 |

### 运行
| 命令 | 作用 |
|------|------|
| `/status` | 查看监控运行状态 |
| `/run` | 立即执行监控（全部） |
| `/run 1` / `/run k1` | 跑指定关键词（按编号 / 按位置） |
| `/run t1` | 跑指定任务组（按位置） |
| `/run agent_jd_30w` | 按任务名/关键词文本运行 |
| `/help` | 查看帮助 |

**`/run` 的 id 规则**：
- `k<n>` / `t<n>` — 显式按类别（关键词/任务组）位置
- `<n>` — 全局编号（先 keywords 后 tasks）
- `<关键词>` / `<任务名>` — 文本精确匹配
- 留空 — 跑全部

授权用户：`ou_8ce2c84aa949a2b28cae7d7de3b0a0c6`

## 数据模型

[tasks.json](tasks.json) 双轨并存（新旧兼容）：

```json
{
  "keywords": ["奉贤相亲", "上海相亲"],
  "tasks": [
    {
      "name": "agent_jd_30w",
      "description": "agent开发工程师的jd，年薪大于30w",
      "keywords": ["agent开发", "AI工程师", "高薪招聘", "JD"],
      "filter": "年薪>30w 的 agent 开发工程师招聘信息；不含培训/课程/广告"
    }
  ],
  "max_per_keyword": 10,
  "push_user_id": ""
}
```

- `keywords[]`：老路径，仅按关键词命中推送（无 LLM 过滤）
- `tasks[]`：新路径，LLM 拆解 + 帖子级 LLM 二次过滤
- pipeline 一次执行同时遍历两组

## LLM 配置

`llm.py` 默认走 DeepSeek 兼容端点（[config.py:75-78](config.py#L75-L78)）：

```bash
# .env
XHS_LLM_API_KEY=sk-xxxxx
# 可选：自定义端点
XHS_LLM_BASE_URL=https://api.deepseek.com/v1
XHS_LLM_MODEL=deepseek-chat
```

- 未配置 `XHS_LLM_API_KEY` → `LLM_ENABLED=False` → pipeline 自动跳过 LLM 过滤（降级到纯关键词去重）
- `manage.py run --no-llm` → 强制跳过 LLM 过滤
- LLM 异常时 `filter_posts()` 降级返回全部帖子（log warning，不中断）

## 远程部署

- 主机：`dev@192.168.39.240` (Ubuntu 24.04)
- 项目目录：`~/xhs_monitor`
- systemd 服务：
  - `xhs-monitor-bot.service`（常驻）— 含 `EnvironmentFile=/home/dev/xhs_monitor/.env` 注入 API key
  - `xhs-monitor-pipeline.timer`（每 6h 触发一次）
- 启用 linger：`loginctl enable-linger dev`

```bash
# 重新加载服务（修改 .service 后）
systemctl --user daemon-reload
systemctl --user restart xhs-monitor-bot.service

# 实时日志
journalctl --user -u xhs-monitor-bot.service -f
```

详见 [docs/OPS.md](docs/OPS.md) 和 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 本地运行

```bash
# 安装依赖
python -m pip install playwright requests openai
python -m playwright install chromium

# 配置 LLM（可选；不配则走纯关键词路径）
echo 'XHS_LLM_API_KEY=sk-xxxxx' > .env

# 获取 Cookie（有头浏览器手动登录）
python save_cookies.py

# 运行监控
python manage.py add "AI Agent 招聘"
python manage.py run
```

## 关键技术决策

- **搜索**：Playwright + 系统 Chrome（Windows channel=chrome，Linux executable_path=/snap/bin/chromium）
- **详情解析**：`page.evaluate` 解析 `window.__INITIAL_STATE__.note.noteDetailMap[noteId]`，不用 requests（被反爬），不用正则（匹配到备案信息）
- **xsec_token**：搜索结果链接 `/search_result/{noteId}?xsec_token=xxx` 必须带 token 访问
- **去重**：SQLite 双层（noteId 主键 + content_hash 内容指纹）
- **LLM 客户端**：openai Python SDK，base_url 切换兼容端点（DeepSeek / OpenAI / 月之暗面 / 智谱 等）
- **LLM 拆解 prompt**：在 [llm.py:21-32](llm.py#L21-L32)，要求返回严格 JSON（name + keywords + filter）
- **推送**：lark-cli im +messages-send，bot 身份 P2P 私聊
- **跨平台**：Windows 用 Git Bash 执行 lark-cli，Linux 直接执行
- **进程管理**：systemd --user + linger，支持主机重启后自动恢复
- **环境注入**：`EnvironmentFile=` 注入 `.env`，避免敏感 key 写入 unit 文件
