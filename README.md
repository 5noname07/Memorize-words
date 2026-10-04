# Memorize-words
This repository is managed with Git and Codex.

## 词间：手机上的英语单词练习

适合 3～4 人使用的网页应用。手机、平板、电脑浏览器共用同一服务端；无需安装手机 App。界面为中文，不依赖第三方账号、在线词典或 API Key。

### 已实现的功能

- 私有账户登录；账户由管理员创建，无公开注册入口。每个账户的单词、批次、权重、错题和记录独立保存。
- 批次新增、查询、改名、删除；单词新增、查询、编辑、移动、删除；CSV 批量导入。
- 中文释义提示、音标显示、英文拼写检查；忽略首尾空格、大小写，统一 Unicode 全角字符，内部拼写和标点仍须匹配。
- 正常学习与错题练习两个独立入口，各自设置批次权重。先按权重抽批次，再在批次内等概率抽单词；权重 0 表示暂停，范围 0～1000。
- 答错自动进入错题集；错题练习答对仍保留，记牢后手动移除。后来再次答错会重新进入。
- 错题按**首次导入的批次 ID**分组，保留首次导入时的批次名称。移动单词、批次改名或删除都不会改变这个来源。
- 学习记录展示最近 100 次答题及累计统计；支持导出个人 JSON 数据，以及管理员完整数据库备份。

正常学习仅从未删除的单词库抽题，不额外把错题集混入。答错的词若仍在正常单词库中，仍可能在正常学习中出现；删除单词/批次后，它们不再参加正常学习，已有错题和学习记录继续保留。

### 技术方案

Python 3.13、Flask、Waitress、SQLite，加原生 HTML/CSS/JavaScript。无需 Node 构建或独立数据库服务。数据库采用外键、事务、WAL 和 FULL 同步；重复提交同一道题只记录一次。密码使用 Werkzeug scrypt 哈希；登录 Cookie 为 HttpOnly/SameSite，写入接口有 CSRF 校验，登录有失败限流。服务端按当前登录用户过滤所有数据，不接受客户端指定的数据所有者。

### Windows 本地运行

在项目目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m flask --app app:create_app create-user your-name
.\.venv\Scripts\python.exe app.py
```

创建账户时交互输入密码并确认（10～256 字符），不把密码放进命令行或代码。重复运行 `create-user` 可以创建其他账户；不会创建默认账户或预设密码。当前电脑已安装运行依赖，可以直接执行后两条命令。

访问 `http://127.0.0.1:8000`。默认数据库为 `instance/words.sqlite3`；首次启动自动在同目录生成随机的 `secret.key`。关闭程序、重启电脑、退出账号或重新登录都不会清除数据。不要删除 `instance` 文件夹，也不要把它当作缓存清理。

手机与电脑连接同一可信 Wi-Fi 后，用电脑的局域网 IPv4 地址访问：

```powershell
$env:HOST = '0.0.0.0'
.\.venv\Scripts\python.exe app.py
```

手机打开 `http://电脑IPv4地址:8000`。用 `ipconfig` 查看 Wi-Fi 的 IPv4 地址；如 Windows 防火墙询问，仅允许私有网络。电脑需保持开机并运行服务。手机不保存数据库，不支持离线答题；数据写入失败时会提示，不能把未确认成功的请求视为已经保存。

### 长期部署与 HTTPS

异地使用时，把服务部署在一台长期运行的服务器上，设置域名和 HTTPS 反向代理。GitHub 仅存放代码，GitHub Pages 无法运行这个有数据库的后端；本项目尚未发布公网服务。

Linux 服务器启动示例（将路径替换为自己的部署目录）：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
export DATABASE=/srv/memorize-words/instance/words.sqlite3
export HOST=127.0.0.1
export PORT=8000
export COOKIE_SECURE=1
.venv/bin/python -m flask --app app:create_app create-user your-name
.venv/bin/python app.py
```

使用 systemd 或托管服务的进程管理器保持 `python app.py` 运行，工作目录指向项目目录；反向代理将 HTTPS 域名请求转发到 `127.0.0.1:8000`。不要使用 Flask debug/开发服务器对外提供服务。Waitress 是生产 WSGI 服务：[Flask 部署说明](https://flask.palletsprojects.com/en/stable/deploying/waitress/)。

数据库目录必须位于**持久化磁盘/数据卷**，不要放在部署时被替换的代码目录或临时容器层。运行账户需要该目录的读写权限。建议定期备份并保留异地副本；磁盘损坏或误删仍需依靠备份恢复。当前规模使用一个应用实例即可。

### 配置和敏感信息

可选环境变量见 `.env.example`。它仅是示例，程序不会自动加载 `.env`。

| 变量 | 默认值 / 用途 |
| --- | --- |
| `DATABASE` | 项目内 `instance/words.sqlite3`；建议使用绝对路径 |
| `SECRET_KEY` | 未设置时使用数据库目录的 `secret.key`；自行设置须至少 32 字符 |
| `COOKIE_SECURE` | `0`；部署到 HTTPS 时设为 `1` |
| `HOST` | `127.0.0.1`；可信局域网测试可设为 `0.0.0.0` |
| `PORT` | `8000` |

改变密钥会使旧登录会话失效，但不会删除学习数据。数据库、密钥、备份、真实 `.env`、虚拟环境和测试产物已被 `.gitignore` 排除。不要将真实密码、数据库、个人导出数据或凭据加入 Git；若自定义数据库路径，应放在 Git 仓库外。

### 备份、恢复和重置密码

完整备份（文件名必须是未存在的新文件，命令不会覆盖已有文件）：

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app backup backups/words-2026-10-05.sqlite3
```

该命令使用 SQLite 在线备份 API，会包含 WAL 中已提交的数据，不要在运行中只复制主数据库文件。备份包含全部用户数据和密码哈希，应按敏感文件保管，保留多个日期版本并复制到另一块磁盘。`secret.key` 单独安全保存；如果使用环境变量密钥，则安全保存对应部署配置。

恢复步骤：停止服务；保留现有整个数据库目录的副本；将完整数据库备份复制到一个**新的**目录，设置 `DATABASE` 指向它并重新启动。不要覆盖或混用旧数据库的 `-wal`/`-shm` 文件。如果没有恢复原密钥，会生成新密钥，用户重新登录即可，学习数据仍保留。完整恢复在独立临时数据库中有自动化测试。

用户忘记密码时：

```powershell
.\.venv\Scripts\python.exe -m flask --app app:create_app reset-password your-name
```

命令交互输入新密码，撤销该账户的所有登录会话，保留学习数据。个人 JSON 导出仅供查阅或外部迁移，不包含密码/会话，也不是完整数据库恢复文件。

### CSV 格式

选择一个批次，点击「批量导入」，粘贴 CSV 或选择 UTF-8 文件。可使用项目中的 `sample-words.csv`：

```csv
english,meaning,phonetic
apple,苹果,/ˈæpəl/
book,书,/bʊk/
```

三列必填；含逗号的字段用双引号括起。每次最多 5000 行，整批校验后一次性写入；任何一行不合法时不会部分保存。重复导入会新增单词，不会自动覆盖现有单词。

### 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check static/app.js
.\.venv\Scripts\python.exe -m compileall -q app.py tests
```

可选真实浏览器检查：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tests/browser_smoke.py
```

默认使用已安装的 Edge 无头浏览器；其他环境可安装 Playwright Chromium 并设置 `BROWSER_CHANNEL=chromium`。测试使用随机密码及临时数据库，不操作实际学习数据。检查手机登录、批次与导入、答错/错题练习、刷新和切换用户、320/390/768/1280 像素下的横向溢出；截图保存到忽略的 `artifacts` 目录。

Git 全局 ignore 的 `Permission denied` 沙箱警告与应用无关，本项目没有修改全局 Git 配置、远程地址或 Windows 用户目录权限。
