# 来源与第三方许可

## 项目主体：Datawhale 旅行规划示例的改编

Trip Agent 基于 Datawhale 社区及 hello-agents 项目贡献者的「HelloAgents 智能旅行助手」示例演进。本仓库对该示例进行了修改，并由 bahua1220 及本项目贡献者持续扩展。

- 原示例：[code/chapter13/helloagents-trip-planner](https://github.com/datawhalechina/hello-agents/tree/12d19487db091770dbcc13a7580c4897b0ca7270/code/chapter13/helloagents-trip-planner)。
- 核对基准：上游提交 `12d19487db091770dbcc13a7580c4897b0ca7270`。
- 原示例 README 明确声明 `CC BY-NC-SA 4.0`，上游 [LICENSE.txt](https://github.com/datawhalechina/hello-agents/blob/12d19487db091770dbcc13a7580c4897b0ca7270/LICENSE.txt) 提供协议全文。
- 本仓库项目代码、文档及对原示例的改编沿用 **CC BY-NC-SA 4.0（署名—非商业性使用—相同方式共享）**，全文见 [LICENSE](LICENSE)。下文明确标注的第三方内容保留各自许可。

主要修改包括：从 HelloAgents / MCP 主流程迁移到 LangChain / LangGraph 与 REST 服务适配；增加多城市路线和分段交通；完善景点候选、餐饮匹配、时间约束及编辑重排；加入微信小程序、账号与历史行程、持久化任务和回归测试。上游作者仍保有原始作品的权利，本项目不代表 Datawhale 或其他上游项目的官方产品或背书。

许可的简明说明见 [Creative Commons 官方页面](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans)。这份声明不授予上游许可之外的商业使用权限；需要超出许可范围使用相关内容时，应向相应权利人取得授权。

## DCloud 模板：MIT

`miniapp/index.html` 的入口模板正文与 DCloud [hello-uniapp](https://github.com/dcloudio/hello-uniapp/blob/ab80c34681013d844be2a9feb362bf96ffeca3ee/index.html) 一致（忽略换行格式）。该部分保留 DCloud 的 MIT 许可：

> Copyright (c) 2018 DCloud

许可全文保存在 [LICENSES/DCloud-MIT.txt](LICENSES/DCloud-MIT.txt)。这部分原始模板不改标为 CC；本项目新增的业务页面及逻辑按项目主体许可发布。导出的小程序不包含原模板中未使用的示例组件、第三方静态库或编译产物。

## 软件依赖

本仓库通过 `backend/requirements.txt`、`frontend/package.json` 与 `frontend/package-lock.json` 引用依赖，不随源码打包分发虚拟环境、`node_modules` 或构建后的依赖代码。各依赖的原始版权与许可保持不变，不因项目主体采用 CC BY-NC-SA 4.0 而改变。

本次核对的 Python 直接依赖许可声明：

| 许可 | 依赖 |
| --- | --- |
| MIT | langgraph、langchain、langchain-openai、langchain-core、fastapi、pydantic、pydantic-settings、loguru、PyJWT、PyMySQL |
| BSD-3-Clause | uvicorn、httpx、python-dotenv |
| Apache-2.0 | python-multipart、huggingface_hub |
| Apache-2.0 AND MIT | aiohttp，包含其随包提供的第三方许可 |
| Apache / BSD 双许可及历史代码说明 | python-dateutil，以安装包内 LICENSE 为准 |

Web 的直接依赖包括 Vue、Vue Router、Ant Design Vue、Axios、Day.js、html2canvas、jsPDF 和高德 JS API Loader；构建依赖包括 Vite、TypeScript 等。锁文件中 TypeScript 声明为 Apache-2.0，其他直接依赖声明为 MIT。传递依赖还包含 BSD、ISC、Zlib 及多许可选项，完整核对范围见 [许可核对记录](docs/LICENSING.md)。

如果另外分发 Web 构建包、容器、安装包或打包依赖的程序，应同时携带实际打包版本要求的版权和许可文件，而不只复制本项目的 LICENSE。

## 框架与服务来源

- [HelloAgents](https://github.com/jjyaoao/HelloAgents)：早期参考与框架使用来源，当前 requirements 不再直接依赖 hello_agents。
- [amap-mcp-server](https://github.com/sugarforever/amap-mcp-server)：早期集成来源，当前以高德 REST 服务适配为主，未随本仓库分发该服务源码。
- 高德地图、微信、Open-Meteo、Tavily、Unsplash 及模型供应商：外部数据或服务来源，按各自服务条款使用。

项目许可不覆盖供应商的服务权限、地图或图片素材权利、用户数据及商标。当前源码目录不提供 API 密钥、用户数据库、原始生成日志、截图素材或预订服务。
