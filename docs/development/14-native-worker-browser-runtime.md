# 正式 Worker 原生浏览器运行方案

> 版本：V0.1  
> 日期：2026-11-24（Asia/Hong_Kong）

> 当前状态目录、Windows 外置运行时和完整启停顺序请以 [运行环境、依赖与服务器启停手册](59-runtime-startup-and-dependency-runbook-20261126.md) 为准。

## 结论

正式 Worker 不再依赖受限 Agent/Codex 子进程启动。所有需要 Playwright 的下载、目录发现和月报补链任务，统一通过原生服务进程运行：

- macOS 开发/单机：普通 Terminal 前台运行或用户级 `launchd`；
- Windows 共享版：NSSM 托管的 Windows Service；
- Worker 启动前强制执行浏览器预检，预检失败时退出，不领取任务。

预检只启动本地浏览器并创建空白页面，不访问外部网站；真正任务仍由现有 Worker、适配器和数据库事务处理。

## 新增入口

| 文件 | 用途 |
|---|---|
| `platform/api/app/worker/browser_preflight.py` | 尝试 Chrome、Edge、自带 Chromium，返回实际渠道和版本 |
| `platform/scripts/run_worker_native.sh` | 原生 Terminal/launchd 的统一 Worker 入口 |
| `platform/scripts/install_worker_launchd.sh` | 安装并加载 macOS 用户级 launchd Agent |
| `platform/scripts/uninstall_worker_launchd.sh` | 卸载 launchd Agent |
| `platform/scripts/install_worker_service.ps1` | 使用 NSSM 安装 Windows Service |
| `platform/scripts/uninstall_worker_service.ps1` | 卸载 Windows Service |

`platform/scripts/worker.sh` 仍保留 `start/stop/restart/status/once/logs`，但 `start`、`once` 已改为调用原生入口；新增 `foreground` 和 `browser-check`。

## macOS 使用方式

### 1. Terminal 前台运行

在系统 Terminal（不是受限 Agent/Codex shell）执行：

```bash
./platform/scripts/worker.sh browser-check
./platform/scripts/worker.sh foreground
```

后台启动仍可使用：

```bash
./platform/scripts/worker.sh start
./platform/scripts/worker.sh status
./platform/scripts/worker.sh logs
```

如果浏览器预检失败，Worker 会返回退出码 `78`，并在日志中保留每个浏览器渠道的失败原因。

### 2. launchd 常驻运行

```bash
./platform/scripts/install_worker_launchd.sh
launchctl print "gui/$(id -u)/com.gf.fmr.worker"
tail -f $PLATFORM_RUNTIME_ROOT/worker-launchd.log
```

卸载：

```bash
./platform/scripts/uninstall_worker_launchd.sh
```

安装脚本生成 `~/Library/LaunchAgents/com.gf.fmr.worker.plist`，设置：

- `RunAtLoad=true`、`KeepAlive=true`；
- `PYTHONPATH`、数据库连接、项目根目录和 Playwright 浏览器目录；
- `--browser-preflight`；
- 标准输出/错误日志写入 `$PLATFORM_RUNTIME_ROOT/`。

必须以实际登录用户安装，因为用户级 launchd 才能访问该用户的 Chrome/Edge 和 Playwright 缓存。若要无人登录启动，应改为系统级服务账号，并预先为该账号安装浏览器和 Playwright 缓存。

## Windows 使用方式

Windows 运行时将 Chromium 放在 `$env:FACTSHEET_RUNTIME_ROOT\ms-playwright`。正式服务使用 NSSM 承接 Python 进程的服务控制协议：

1. 将经过批准的 `nssm.exe` 放到 `$env:FACTSHEET_RUNTIME_ROOT\bin\nssm.exe`，或通过 `-NssmPath` 指定路径；
2. 以管理员 PowerShell 执行：

```powershell
$env:FACTSHEET_STATE_ROOT = 'D:\FundResearchAgent\state'
$env:FACTSHEET_RUNTIME_ROOT = "$env:FACTSHEET_STATE_ROOT\runtime"
$env:PLATFORM_RUNTIME_ROOT = "$env:FACTSHEET_STATE_ROOT\platform-runtime"
.\platform\scripts\install_worker_service.ps1 `
  -RuntimeRoot $env:FACTSHEET_RUNTIME_ROOT `
  -DatabaseUrl "postgresql+psycopg://worker@127.0.0.1:5432/fund_research"
```

脚本会配置 `AppDirectory`、数据库连接、`PLAYWRIGHT_BROWSERS_PATH`、日志目录和自动重启，并以 `--browser-preflight` 启动 Worker。

卸载：

```powershell
.\platform\scripts\uninstall_worker_service.ps1
```

Windows Service 的登录身份必须具备：

- 项目目录、`$env:FACTSHEET_DATA_ROOT`、`$env:PLATFORM_RUNTIME_ROOT` 的读写权限；
- Playwright 浏览器目录的执行权限；
- PostgreSQL、基金管理人网站、MinerU/Qwen 等外部服务的网络权限。

## 启动契约

正式 Worker 支持两种等价开关：

```text
--browser-preflight
PLATFORM_BROWSER_PREFLIGHT=required
```

两者任一开启都会执行预检。浏览器尝试顺序由 `PLATFORM_BROWSER_CHANNELS` 控制，默认值为：

```text
chrome,msedge,bundled
```

Windows 通常只需 `bundled`；macOS 优先使用 Chrome/Edge，以减少站点对自动化浏览器的拦截。

## 验收标准

1. `worker.sh browser-check` 在原生 Terminal 返回浏览器渠道和版本；
2. launchd/Windows Service 重启后 Worker 自动恢复并继续轮询；
3. Playwright 适配器可完成至少一只 HSBC、富达或瑞银基金的目录/下载抽样；
4. 受限进程启动时不再静默领取任务，而是以退出码 `78` 和明确日志失败；
5. API 任务状态、租约恢复和单写锁行为不变。

## 已知边界

- 浏览器预检只验证本地启动，不保证目标网站未改版或未反爬；
- `requirements.lock.txt` 当前锁定 Playwright 1.61.0，开发机安装为 1.62.0，正式打包前应统一版本并重新生成浏览器缓存；
- Windows 安装脚本依赖公司批准的 NSSM，不在仓库内携带第三方二进制。

