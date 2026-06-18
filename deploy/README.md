# deploy/

本目录存放远程部署相关的脚本和 systemd unit 文件。

部署到远程服务器（dev@192.168.39.240）的步骤：

```bash
# 1. 复制 systemd unit 文件
scp deploy/xhs-monitor-bot.service dev@192.168.39.240:~/.config/systemd/user/
scp deploy/xhs-monitor-pipeline.service dev@192.168.39.240:~/.config/systemd/user/
scp deploy/xhs-monitor-pipeline.timer dev@192.168.39.240:~/.config/systemd/user/

# 2. 启用并启动
ssh dev@192.168.39.240 '
    systemctl --user daemon-reload
    systemctl --user enable xhs-monitor-bot.service xhs-monitor-pipeline.timer
    systemctl --user start xhs-monitor-bot.service xhs-monitor-pipeline.timer
    loginctl enable-linger dev
'

# 3. 配置飞书 CLI（仅首次需要）
ssh dev@192.168.39.240 '
    export PATH=$HOME/.npm-global/bin:$PATH
    echo "<APP_SECRET>" | lark-cli config init --app-id cli_aab9e9b37f3bdcfc --app-secret-stdin --brand feishu
'

# 4. 安装 Python 依赖
ssh dev@192.168.39.240 '
    python3 -m pip install --user --break-system-packages playwright requests
    mkdir -p ~/.npm-global && npm config set prefix ~/.npm-global
    npm install -g @larksuite/cli
'
```

详见 [docs/OPS.md](../docs/OPS.md) 完整部署指南。
