# 项目名称 KHEX基金数据爬取工具

## 项目指纹
- **技术栈**:
  - Python 3.11+（运行时位于外置状态目录 `FACTSHEET_VENV_ROOT` / `FACTSHEET_RUNTIME_ROOT`；代码包不携带依赖）
  - requests / playwright(Chromium 无头) / openpyxl / pypdf / PyMuPDF / opencc-python-reimplemented
  - 外部服务：MinerU vlm 精准解析 API；Qwen/DashScope Chat Completions（qwen3.7-max 主抽取、qwen3.7-max-2026-06-08 fallback、qwen3.7-plus 视觉复核）；open.er-api.com 汇率接口
  - 统一配置：外置状态目录 `config.ini`（`FACTSHEET_CONFIG` 可指定，环境变量可覆盖）；代码包只保留 `config.example.ini`
- **架构**:
  - 三段流水线：
    1) 下载：code/run_all_downloaders.py + code/companies/<公司>/ 130 个链接补全脚本（另含各公司下载器），按外置状态目录 `inputs/爬虫list.xlsx` 生成任务，5 家公司并发、同公司串行，失败自动重试；输出 `$FACTSHEET_DATA_ROOT/<批次>/factsheet/<类别>/` PDF 与 JSON 报告
    2) 解析：code/run_all_mineru.py 串行调用 MinerU vlm 精准模式，缓存至 data/<批次>/markdown/<类别>/<基金__hash>/（full.md + content_list.json + images + manifest.json）
    3) 结构化：code/run_all_structured_data.py 编排固收/权益/中国精选，Qwen 默认 8 并发抽取，含缺字段精修、fallback 模型、视觉复核、Ticker 证券缓存、汇率换算、校验打标(review)；产出三个 Excel（基金汇总/前十大持仓/地区矩阵/行业矩阵）+ per_pdf JSON/meta + combined CSV/JSON + 运行报告
  - 入口统一走 code/run_with_config.py 加载外置 config.ini；服务器启停顺序为 PostgreSQL → API → Worker → Web，详见 `docs/development/59-runtime-startup-and-dependency-runbook-20261126.md`

## 开发命令
- **环境适配**:
  1. 设置 `FACTSHEET_STATE_ROOT`、`FACTSHEET_VENV_ROOT`、`FACTSHEET_RUNTIME_ROOT`；使用 `config.example.ini` 创建外置 `config.ini`
  2. `"$FACTSHEET_VENV_ROOT/bin/python" -m pip install -r requirements.txt`，再安装 `platform/requirements.txt` 和 Playwright Chromium
- **运行爬虫**:
  1. python code/run_with_config.py run_all_downloaders.py（下载）
  2. python code/run_with_config.py run_all_mineru.py --select-batch（MinerU 转 Markdown）
  3. python code/run_with_config.py run_all_structured_data.py --select-batch（生成三个 Excel）
- **运行测试**: `PYTHONPATH=platform/api:code "$FACTSHEET_VENV_ROOT/bin/pytest" -q`；Web 在 `platform/web` 执行 `pnpm test -- --run && pnpm build`

## 项目规范
1. 本文件./agents/AGENTS.md是本项目的配置文件，请优先加载进上下文
2. 在问答交互中，优先使用计划模式反串讲一遍用户的意图（用序号分点归纳），并给出方案；用户肯定方案可行后才可以改动本地文件和代码
3. 改动本地项目且用户审核后，或长问答后，直接按下面的模版总结这轮对话并追加在./agents/chat.md文件中（用户已确认：归档无需再询问）
```
Q：用户原问题
Intent：输出大模型反串讲用户的意图
Plan：输出大模型给出的方案
Action：输出大模型实际作出的改动，或用户审核且手动修改后的改动，代码显示格式如下：
修改代码所处的函数名Method：
+++修改后的核心代码
---修改之前的核心代码
```
4. 给出方案时判断一下哪些操作用户手动执行能节约token，特别是编辑表格文件等用其他软件更方便的操作
5. 自行判断是否是合适时机可以压缩上下文
6. 补全脚本（*_fund_link.py）的 CLI 端到端验证一律用 `/tmp` 副本（`--crawler-list` 指向副本）；正式表 I/J 必须保持为空，留给用户走 GUI「爬虫补全」验收
7. 每轮只接入一家公司（建表→摸查→脚本→注册→验证→归档），做完再按拼音序取下一家

## 编码规范
1. 所有函数声明处必须有中文注释说明每个参数的含义
2. 每次修改不可以改动除指定逻辑外的代码，要求对原代码修改最小化
3. 每次修改代码后执行编译，若有编译错误则处理报错至编译成功
4. 每次修改后提交代码格式如下，便于审核：
```
#+++
after
#+++
#---
#before
#---
```

## 安全红线
- **只读目录**: 绝对不要修改本项目目录之外的任何文件。
- **禁止操作**: 严禁执行 `rm -rf /` 等危险命令。
