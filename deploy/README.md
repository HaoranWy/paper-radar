# 在现有 Linux 服务器运行

先根据主 README 创建虚拟环境并手动完成一次真实采集。随后用 `crontab -e` 添加以下示例；将目录替换为真实绝对路径：

```cron
17 8 * * * cd /absolute/path/paper-radar && .venv/bin/python run.py >> /absolute/path/paper-radar/radar.log 2>&1
```

此示例按照**服务器 cron 的时区**在 08:17 运行，不会从 `config.yaml` 自动读取时区。首次请核对 `timedatectl`、`date` 和实际执行日志。不要在 GitHub Actions 和服务器同时写同一套 data 文件。

将静态网站服务的根目录指向 `/absolute/path/paper-radar/site`。本地调试可运行：

```bash
python -m http.server 8080 --bind 127.0.0.1 --directory site
```

该调试命令仅监听回环地址。正式公网访问应使用现有的 HTTPS 反向代理/静态站点服务，不要直接暴露整个项目目录、`.env` 或日志。私人部署则通过现有访问控制或 SSH 端口转发访问。

定时任务需要服务器在线及源站网络连通。脚本仅执行一次采集；单独打开网页并不会激活定时任务。
