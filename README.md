# Trip Agent · 智能旅行规划

面向单城市和多城市旅行的 AI 应用。模型参与理解和选择候选，程序结合地图路线、用餐时段、开放时间和抵离约束，生成可编辑的每日时间轴。

本仓库展示的是持续迭代中的工程项目，不承诺任意请求都能生成可执行行程。交通与开放时间存在来源及估算边界，出发前仍需核实。

## 已实现

- 单城市、多城市路线建议与路线确认，支持选择抵达/离开枢纽和时间。
- 景点候选检索、去重、区域归并和筛选，先考虑游览，再匹配周边餐饮。
- 按交通偏好计算逐段路线；跨城接驳与城际段分开展示。
- 酒店、景点、餐饮与交通时间轴，编辑后重新校验和排程。
- 后台生成任务、状态轮询、任务恢复、账号登录与历史行程管理。
- 确定性约束校验、质量报告、离线回归和显式开启的小批量真实评测。

尚未实现：与命名助手对话收集需求、通过自然语言修改整份行程、真实铁路班次/余票查询、旅游产品预订支付。不要把未来计划写成已经上线的功能。

## 实际技术栈

后端使用 Python、FastAPI、Pydantic、LangChain / LangGraph、MySQL。模型通过 `ChatOpenAI` 适配兼容接口；地图和路线以高德 REST API 为主，天气使用 Open-Meteo 并提供备用来源，Tavily 用于部分信息补充与异常城际耗时查询。

Web 端使用 Vue 3、TypeScript、Vite、Ant Design Vue；微信小程序端使用 uni-app / Vue 3，通过 HBuilderX 编译。当前实现不是旧 README 中的 HelloAgents + 高德 MCP 主流程，也不是模型随意输出一篇攻略。

详见 [架构与约束](docs/ARCHITECTURE.md)、[本地运行](docs/DEVELOPMENT.md)、[安全说明](SECURITY.md)。

## 目录

```text
backend/app/                 API、规划器、数据模型及外部服务适配
backend/scripts/             离线测试与真实评测入口
backend/tests/               回归用例、固定数据与验收说明
frontend/                    Web 客户端
frontend_miniapp_patch/      小程序页面/API 补丁及无网络逻辑测试
miniapp/                     仅整理导出时生成的小程序源码快照
scripts/                     开源前扫描及独立快照准备工具
docs/                        架构、运行与发布检查说明
```

开发工作区中的完整小程序原先位于独立 HBuilderX 项目；`frontend_miniapp_patch` 本身不是可独立编译的小程序。整理工具可把实际使用的页面/API/应用入口合并进 `miniapp/`，不包含原工程中的历史构建产物及未用到的示例组件。

## 本地启动

需要 Python、Node.js、MySQL，以及自己的地图和模型配置。当前本地验证环境为 Python 3.12、Node.js 24；尚未进行完整多版本兼容性测试。

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 按 docs/DEVELOPMENT.md 填入本地配置，并准备 MySQL
python run.py
```

Web 端在另一个终端启动：

```powershell
cd frontend
npm ci
Copy-Item .env.example .env.local
npm run dev
```

生成真实行程会访问模型/地图等外部服务，可能产生费用。配置不完整时不要使用演示的占位值连接生产服务。

## 测试与评测

从项目根目录执行：

```powershell
python backend/scripts/run_offline_tests.py
python backend/scripts/run_acceptance.py --suite all
node --test frontend_miniapp_patch/tests/*.test.cjs
python -m unittest discover -s scripts/tests -v
```

离线入口会禁止外网连接；模型、地图、身份及数据库等部分环节使用固定适配器或替身。它们验证已编码的业务约束，不等于真实数据质量、MySQL、生产性能或微信真机验收通过。

- [八条验收与测试边界](backend/tests/ACCEPTANCE.md)
- [真实评测说明](backend/tests/REAL_EVALUATION.md)：默认仅预览请求；付费执行需要显式参数。
- [四天行程回归说明](backend/tests/FOUR_DAY_REGRESSION.md)

不在首页写未经持续验证的成功率、性能或用户规模；实际测试数量和结果以当次执行报告为准。测试报告、原始日志和用户行程不纳入公开仓库。

## 发布准备

本地工作区曾位于包含其他项目的父级 Git 仓库中，且运行配置进入过旧历史。因此不能直接在父仓库 `git add .` 后推送。

```powershell
# 只读扫描：原工作区含私密配置时返回非零是预期的风险提示
python scripts/repository_audit.py --history --output .codex_staging/github-preparation/source-audit.json

# 生成全新、无旧 Git 历史的本地候选目录；不会创建 GitHub 仓库或推送
python scripts/prepare_github_snapshot.py --output .codex_staging/github-preparation/candidate
# 如有完整小程序源码，可额外传入 --miniapp-source <该项目目录>
```

导出物仍需人工确认来源授权、演示资料和扫描范围后，才决定是否公开。详见 [发布检查清单](docs/PUBLISHING.md)。

## 来源与授权

项目早期基于 HelloAgents 旅行规划示例演进。保留以下来源线索，不将整个项目冒充为全部从零编写：

- [Hello-Agents 教程](https://github.com/datawhalechina/Hello-Agents)
- [HelloAgents 框架](https://github.com/jjyaoao/HelloAgents)
- [早期引用的高德 MCP 服务](https://github.com/sugarforever/amap-mcp-server)
- uni-app / HBuilderX 及各实际使用依赖的原作者。

原 README 声明过 CC BY-NC-SA 4.0，但当前缺少项目级 LICENSE，其他模板元数据也不能代表全部代码的授权。此次整理不擅自改成 MIT 或其他许可。公开前需核对上游来源与适用许可，再补齐 LICENSE / NOTICE；目前不能把本项目描述为已完成授权核验的开源发行版。

