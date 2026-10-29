# Trip Agent · 智能旅行规划

支持单城市和多城市旅行的 AI 行程规划工具，提供 Web 与微信小程序。填写目的地、出行日期和偏好后，生成包含景点、餐饮、住宿及逐段交通的每日安排，并支持继续编辑和保存。

使用 **Python + FastAPI + LangChain / LangGraph + MySQL** 构建后端，Web 使用 **Vue 3 + TypeScript**，小程序使用 **uni-app**。模型参与路线和候选选择，地图服务与排程规则共同完成时间安排。

## 核心功能

- **旅行需求设置**：填写目的地、日期、兴趣、住宿和交通偏好，选择游玩节奏；已经确定车次或航班时，可补充抵离时间及车站、机场。
- **单城市行程**：围绕景点所在区域组织游览，结合开放时间和交通耗时安排餐饮、住宿与每日活动。
- **多城市路线**：推荐并确认城市顺序，将换城时间计入日程；分别展示出发地接驳、城际交通和到达后的接驳。
- **时间轴与地图**：查看每天的活动顺序、开始结束时间、游览时长和相邻地点之间的交通，结合地图了解位置与路线。
- **行程编辑**：添加、删除或搜索替换景点，调整部分路段的交通方式，重新计算安排并检查时间冲突。
- **账号与历史行程**：支持账号密码及微信小程序快捷登录；后台生成任务提供进度查询，完成后保存到账号，方便后续查看和编辑。

使用时，先确定目的地和日期，多城市旅行再确认路线；填写偏好并登录后生成行程，最后按自己的想法调整细节。

## 工程实践

- **景点候选清洗与归并**：对地点检索结果进行类型过滤、别名去重、区域归并和打分，区分大景区与独立子景点，再交给模型选择每日候选。
- **景点与餐饮分阶段安排**：先确定游览内容，再查找周边餐厅，结合餐次、营业时间和绕行成本选择门店；餐饮安排与交通一起参与后续重排。
- **时间约束校验与修复**：把抵离缓冲、入住退房、游览、用餐和交通放入同一时间轴，检查重叠、闭馆及衔接冲突，通过换日、替换候选和重排修复安排。
- **交通偏好与编辑联动**：保留用户对具体路段的交通选择，编辑后重新核算时间；跨城日同时考虑上一座城市的结束时间与下一座城市的可用时段。
- **后台任务与状态隔离**：MySQL 保存任务及历史行程，客户端通过轮询获取进度；每次规划和编辑使用独立业务状态，复用模型客户端，减少不同用户或任务之间的相互影响。
- **分层回归测试**：以固定数据验证排程规则、账号及任务隔离、HTTP 任务流程和前端编辑行为，再用显式开启的真实接口评测检查模型与外部数据表现。

实现细节见 [架构说明](docs/ARCHITECTURE.md)，具体场景见 [业务验收](backend/tests/ACCEPTANCE.md)。

## 本地启动

建议先运行后端和 Web，再配置微信小程序。当前开发环境为 **Python 3.12、Node.js 24**；还需准备 MySQL，以及自己的模型服务和高德地图 Key。

以下命令以 Windows PowerShell 为例，Linux / macOS 的命令及完整配置见 [开发指南](docs/DEVELOPMENT.md)。

### 1. 获取代码

~~~powershell
git clone https://github.com/bahua1220/trip-agent.git
cd trip-agent
~~~

### 2. 后端

先启动 MySQL，准备项目开发账号。首次启动会创建数据库和表，账号需要相应的初始化与读写权限。

~~~powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
~~~

在 `backend/.env` 中填写：

| 配置 | 用途 |
| --- | --- |
| `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID` | 模型服务凭证、接口地址和模型名称 |
| `AMAP_API_KEY` | 高德 Web 服务 Key，用于地点与路线查询 |
| `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD` | MySQL 连接 |
| `AUTH_SECRET_KEY` | 自行生成的随机签名密钥 |

配置完成后启动：

~~~powershell
.\.venv\Scripts\python.exe run.py
~~~

后端默认运行在 `http://localhost:8000`，可访问 `/health` 检查启动状态。本地需要接口文档时，设置 `DEBUG=true` 并重启，访问 `/docs`。

### 3. Web

另开终端，在仓库根目录执行：

~~~powershell
cd frontend
npm ci
Copy-Item .env.example .env.local
~~~

将 `VITE_API_BASE_URL` 设置为自己的后端地址，本地一般为 `http://localhost:8000`；地图展示使用自己的 `VITE_AMAP_WEB_JS_KEY`。随后：

~~~powershell
npm run dev
~~~

打开终端显示的网址（默认 `http://localhost:5173`），注册账号后即可开始规划。只运行 Web 可以使用账号密码登录，无需先配置微信。真实生成会消耗所配置模型、地图及搜索服务的额度。

### 4. 微信小程序（可选）

1. 在 HBuilderX 导入 `miniapp/`，在 `manifest.json` 填写自己的应用与微信 AppID。
2. 修改 `miniapp/api/index.js`，将 `https://api.example.com` 替换为自己的 HTTPS 后端地址。
3. 在后端配置 `WECHAT_APP_ID`、`WECHAT_APP_SECRET`，在微信后台配置合法域名及隐私说明。
4. 通过 HBuilderX 编译到微信开发者工具，再使用体验版进行真机测试。

小程序使用 `miniapp/` 工程；`frontend_miniapp_patch/` 用于维护页面补丁与逻辑测试。更多配置和排错步骤见 [开发指南](docs/DEVELOPMENT.md#微信小程序)。

## 测试

在仓库根目录执行后端离线回归和小程序页面逻辑测试：

~~~powershell
.\backend\.venv\Scripts\python.exe backend/scripts/run_offline_tests.py
node --test frontend_miniapp_patch/tests/*.test.cjs
~~~

测试场景包括无抵离时间、午间抵达、清晨离开、闭馆换日、餐厅绕行、午后跨城和编辑重排。离线测试使用固定数据并拦截外网请求，不消耗真实接口额度。

需要检查真实模型与地图返回结果时，使用 [真实接口评测](backend/tests/REAL_EVALUATION.md)；该入口默认只准备请求，付费执行需要显式开启。Web 构建可在 `frontend/` 运行 `npm run build`。

## 项目结构

~~~text
backend/                  API、规划器、服务适配和回归测试
frontend/                 Web 客户端
miniapp/                  微信小程序源码
frontend_miniapp_patch/    小程序补丁与页面逻辑测试
docs/                     架构、开发和维护文档
scripts/                  仓库整理工具及测试
~~~

## 已知限制

- 景点开放时间、餐厅营业时间和预约要求来自外部信息，出发前仍需确认；页面会提示缺失或需要核实的内容。
- 城际铁路用时是规划参考，不对应实时车次与余票；清晨、深夜的公共交通还需确认首末班。本项目提供行程规划，不提供订票或酒店下单。
- 后台任务目前使用进程内工作线程与 MySQL 持久化，适合当前单服务部署，多机任务调度尚未接入。

## 致谢与许可

项目起步参考了 [Hello-Agents](https://github.com/datawhalechina/Hello-Agents) 和 [HelloAgents](https://github.com/jjyaoao/HelloAgents) 的旅行规划示例，并持续完善候选选择、交通衔接、排程、编辑和任务管理。感谢相关框架、uni-app 及地图与天气服务的作者和维护者。

项目级许可证正在整理，第三方依赖遵循各自的许可证。

