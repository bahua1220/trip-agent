# 开发指南

本指南面向希望在自己的电脑或服务器运行 Trip Agent 的开发者。Windows 的最短启动流程见 [README](../README.md#自己运行)。

## 环境准备

当前开发与验证使用 Python 3.12、Node.js 24。准备好 MySQL 服务、一个项目开发账号，以及自己的模型和高德地图 Key。

后端目前使用 MySQL 存储账号、行程和任务。首次启动会执行数据库与表的初始化，账号需要创建项目数据库、建表及读写权限。生产部署请单独规划数据库权限与备份。

## 后端配置

把 `backend/.env.example` 复制为 `backend/.env`，在 `backend` 目录启动服务，确保配置文件能正确加载。

| 配置 | 说明 |
| --- | --- |
| `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID` | 模型凭证、兼容接口地址和模型名称 |
| `AMAP_API_KEY` | 高德 Web 服务 Key，用于地点检索与路线查询 |
| `MYSQL_*` | MySQL 主机、端口、库名、账号和密码 |
| `AUTH_SECRET_KEY` | 随机签名密钥；开发环境也请替换示例值 |
| `HOST`、`PORT` | 默认监听 `0.0.0.0:8000` |
| `CORS_ORIGINS` | 允许访问后端的 Web 来源，多个地址用逗号分隔 |
| `DEBUG` | 本地调试可设为 `true`，开启 `/docs`；生产保持 `false` |
| `WECHAT_APP_ID`、`WECHAT_APP_SECRET` | 使用微信登录时配置，AppSecret 只放后端 |
| `TAVILY_API_KEY` | 可选，用于部分公开信息补充和异常跨城耗时查询 |
| `UNSPLASH_ACCESS_KEY` | 可选，用于图片查询 |

仅体验 Web 时可以先使用账号密码注册和登录，不需要预先配置微信账号。接入微信小程序后，再配置微信登录和内容安全相关项。模型、地图等真实请求会使用你配置的供应商额度。

生成签名密钥可以在本机运行以下命令，再把结果填到自己的 `.env`：

~~~powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
~~~

### Windows

~~~powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 填好 backend/.env 后启动
.\.venv\Scripts\python.exe run.py
~~~

### Linux / macOS

~~~bash
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# 填好 .env 后启动
.venv/bin/python run.py
~~~

服务启动后，访问 `http://localhost:8000/health`；开启 `DEBUG=true` 时可访问 `http://localhost:8000/docs`。

## Web 配置

在 `frontend` 目录执行 `npm ci`，复制 `.env.example` 为 `.env.local`：

- `VITE_API_BASE_URL`：自己的后端地址，本地一般为 `http://localhost:8000`。
- `VITE_AMAP_WEB_JS_KEY`：地图展示使用的高德 Web 端（JS API）Key，与后端 Web 服务 Key 区分。

`VITE_*` 配置会进入浏览器代码，只放客户端允许使用的配置；模型 Key、数据库密码和微信 AppSecret 留在后端。

~~~powershell
npm run dev
~~~

默认开发地址为 `http://localhost:5173`。正式构建使用 `npm run build`，产物在 `frontend/dist/`。如使用自定义域名或端口，记得同步后端的 `CORS_ORIGINS`；部署 Web 时还需支持前端路由回退。

## 微信小程序

GitHub 仓库中的 `miniapp/` 包含当前使用的页面、API 和 Vue 3 应用入口，可以作为 HBuilderX 工程导入。`frontend_miniapp_patch/` 用于维护补丁和逻辑测试，运行小程序请使用 `miniapp/`。

1. 用 HBuilderX 打开 `miniapp/`，在 `manifest.json` 中配置自己的应用与微信 AppID。
2. 修改 `miniapp/api/index.js` 的后端地址，把 `https://api.example.com` 替换为自己的 HTTPS 服务地址。
3. 在后端填写同一小程序的 `WECHAT_APP_ID`、`WECHAT_APP_SECRET`，按需配置微信服务所需的服务器 IP 白名单。
4. 在微信后台配置请求合法域名、隐私说明等应用信息。
5. 使用 HBuilderX 编译到微信开发者工具，检查登录、生成、时间轴、站点选择和编辑，然后通过体验版进行真机测试。

小程序使用 HBuilderX 编译，不使用 Web 目录的 `npm run build`。后端接口地址和 AppSecret 是不同配置：前者供客户端访问，后者仅保存在服务端。

## 测试与评测

从项目根目录运行后端离线回归：

~~~powershell
python backend/scripts/run_offline_tests.py
~~~

如果使用前文创建的虚拟环境，Windows 可把 `python` 换成 `.\backend\.venv\Scripts\python.exe`，Linux / macOS 换成 `backend/.venv/bin/python`。

固定场景验收、小程序页面逻辑与整理工具测试：

~~~powershell
python backend/scripts/run_acceptance.py --suite all
node --test frontend_miniapp_patch/tests/*.test.cjs
python -m unittest discover -s scripts/tests -v
~~~

离线测试拦截外网访问，并使用固定的模型、地图及存储适配器检查业务行为。它适合开发时快速回归；接入自己的数据库和供应商后，再补一次真实生成与客户端体验检查。

真实评测入口默认只准备请求和预览报告，具体付费执行方式见 [真实接口评测](../backend/tests/REAL_EVALUATION.md)。评测输出保存在 `backend/test-results/`，无需提交到仓库。

## 常见问题

- **数据库连接失败**：检查 MySQL 服务、主机、端口、账号密码，以及初始化数据库和表的权限。
- **后端启动但生成失败**：检查模型名称、接口地址、Key 和供应商额度。启动健康检查与真实生成是两条不同链路。
- **Web 连不上后端**：检查 `VITE_API_BASE_URL` 与 `CORS_ORIGINS`；修改前端环境变量后重启开发服务。
- **地图没有显示**：区分高德后端 Web 服务 Key 和客户端 JS API Key，并核对供应商后台的使用限制。
- **微信登录失败**：检查前后端 AppID 是否一致、AppSecret 是否正确，以及后端访问微信服务所需的配置。
- **找不到 `/docs`**：本地设 `DEBUG=true` 并重启；生产默认不开放交互文档。

密钥、用户数据和日志的管理见 [安全说明](../SECURITY.md)；路线、排程和任务管理的实现见 [架构说明](ARCHITECTURE.md)。
