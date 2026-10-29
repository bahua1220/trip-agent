# Trip Agent · 智能旅行规划

填写目的地、出行日期和旅行偏好，生成一份包含景点、餐饮、住宿及交通安排的多日行程。

Trip Agent 支持单城市和多城市旅行，提供 Web 和微信小程序两个客户端。你可以查看每天的时间轴，也可以继续调整景点、餐厅和交通方式。

## 能做什么

- **单城市规划**：根据游玩节奏、兴趣和交通偏好安排每天的活动。
- **多城市规划**：推荐城市游览顺序，把换城时间和两端接驳计入日程。
- **抵达与离开安排**：选择车站或机场，填写抵离时间，预留入住、退房和候车时间。
- **每日时间轴**：按顺序展示游览、用餐、住宿与逐段交通耗时。
- **行程编辑**：添加或删除景点、搜索替换地点、调整交通方式，并重新检查时间安排。
- **任务与历史行程**：后台生成、查看进度，登录后保存和管理自己的行程。

## 怎么使用

1. 选择目的地和出行日期；多城市旅行先确认路线。
2. 设置游玩节奏、兴趣、交通与住宿偏好，按需填写抵离时间和站点。
3. 登录并生成行程，完成后查看每天的地图、时间轴和预算。
4. 按自己的想法编辑行程，保存后可在历史行程中继续查看。

例如，想去南京玩四天、主要坐公共交通、喜欢历史和当地美食，可以直接填写这些条件；如果已经订好高铁，再补充到站和返程信息。

## 怎么规划出来的

模型负责结合需求挑选候选、提出路线和日程建议；地图服务提供地点和交通数据。程序再根据开放时间、用餐时段、交通耗时和抵离时间检查安排，处理重复、绕路和时间冲突。

多城市旅行会把“酒店到车站—城际交通—到站后接驳”分开考虑，而不是直接从前一座城市跳到下一座城市。

更详细的实现见 [架构说明](docs/ARCHITECTURE.md)。

## 技术栈

- **后端**：Python、FastAPI、Pydantic、LangChain / LangGraph、MySQL。
- **Web**：Vue 3、TypeScript、Vite、Ant Design Vue。
- **微信小程序**：uni-app / Vue 3、HBuilderX。
- **外部服务**：高德地图、Open-Meteo、兼容 OpenAI 接口的模型服务；Tavily 用于部分信息补充。

## 自己运行

建议先启动后端和 Web，熟悉流程后再配置微信小程序。当前开发环境为 Python 3.12、Node.js 24；运行还需要 MySQL，以及自己的模型和高德地图配置。

以下命令以 Windows PowerShell 为例；Linux / macOS 的启动方式及完整配置见 [开发指南](docs/DEVELOPMENT.md)。

### 1. 获取代码

~~~powershell
git clone https://github.com/bahua1220/trip-agent.git
cd trip-agent
~~~

### 2. 启动后端

先启动 MySQL，准备一个用于本项目的开发账号。后端首次启动会创建项目数据库和表，账号需要相应权限。

~~~powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
~~~

打开 `backend/.env`，填写以下配置后再启动：

- `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID`：模型服务。
- `AMAP_API_KEY`：高德 Web 服务 Key。
- `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`：数据库。
- `AUTH_SECRET_KEY`：自行生成的随机签名密钥。

~~~powershell
.\.venv\Scripts\python.exe run.py
~~~

默认后端地址为 `http://localhost:8000`，可访问 `/health` 检查是否启动。需要交互式 API 文档时，在本地配置 `DEBUG=true`，重启后访问 `/docs`。

### 3. 启动 Web

在项目根目录另开一个终端：

~~~powershell
cd frontend
npm ci
Copy-Item .env.example .env.local
~~~

在 `frontend/.env.local` 中设置 `VITE_API_BASE_URL=http://localhost:8000`；地图展示使用自己的 `VITE_AMAP_WEB_JS_KEY`。

~~~powershell
npm run dev
~~~

打开终端显示的网址（默认 `http://localhost:5173`），注册账号后即可开始规划。真实生成会调用外部模型和地图服务，具体费用与额度由你配置的供应商决定。

### 4. 配置微信小程序（可选）

使用 HBuilderX 导入 `miniapp/`，填写自己的 AppID，并将 `miniapp/api/index.js` 的后端地址改为自己的 HTTPS 服务地址。示例地址 `https://api.example.com` 需要替换。

微信登录还需要在后端配置 `WECHAT_APP_ID`、`WECHAT_APP_SECRET`；随后配置合法域名，通过 HBuilderX 编译到微信开发者工具。具体步骤见 [小程序配置](docs/DEVELOPMENT.md#微信小程序)。

## 项目结构

~~~text
backend/                  后端 API、规划器、服务适配和测试
frontend/                 Web 客户端
miniapp/                  微信小程序源码
frontend_miniapp_patch/    小程序补丁与页面逻辑测试
docs/                     架构、运行和维护说明
scripts/                  仓库整理工具及测试
~~~

## 测试

在项目根目录运行：

~~~powershell
.\backend\.venv\Scripts\python.exe backend/scripts/run_offline_tests.py
node --test frontend_miniapp_patch/tests/*.test.cjs
~~~

离线测试使用固定数据检查排程规则、任务流程和编辑行为，不消耗真实接口额度。需要查看具体场景或测试真实模型与地图时，可参考 [业务验收](backend/tests/ACCEPTANCE.md) 和 [真实接口评测](backend/tests/REAL_EVALUATION.md)。

## 使用提示

行程用于出行参考，开放时间、预约要求和交通运营情况请在出发前确认。跨城铁路用时是规划参考，不代表已查询到可购买的具体班次；本项目不提供订票或酒店下单。

## 致谢与许可

项目起步参考了 [Hello-Agents](https://github.com/datawhalechina/Hello-Agents) 和 [HelloAgents](https://github.com/jjyaoao/HelloAgents) 的旅行规划示例，在此基础上持续完善路线、排程、编辑和任务管理。感谢相关框架、uni-app 及地图与天气服务的作者和维护者。

项目级许可证正在整理，第三方依赖遵循各自的许可证。

