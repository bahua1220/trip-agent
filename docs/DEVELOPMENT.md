# 本地运行与配置

## 后端

在 `backend` 下创建 Python 虚拟环境、安装 `requirements.txt`，复制 `.env.example` 为 `.env`。当前业务数据路径使用 MySQL；不要因为配置中存在历史 `DATABASE_PATH` 字段就以为 SQLite 已支持同等启动流程。

至少检查以下项：

| 配置 | 用途 |
| --- | --- |
| `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID` | 主模型配置；不完整时真实生成无法正常工作 |
| `AMAP_API_KEY` | 后端地图检索与路线服务 |
| `MYSQL_*` | 本地数据库，使用自己的账号密码 |
| `AUTH_SECRET_KEY` | 自行生成的随机签名密钥，不使用示例值 |
| `HOST`、`PORT`、`CORS_ORIGINS` | 监听和 Web 跨域配置 |
| `DEBUG` | 本地需要 `/docs` 时设为 true；生产不要照搬调试设置 |
| `WECHAT_APP_ID`、`WECHAT_APP_SECRET` | 微信登录和内容安全；AppSecret 只留在服务端 |
| `TAVILY_API_KEY` | 可选的信息补充/异常查询，存在额度消耗 |

当前启动初始化会尝试创建数据库和数据表，开发账号需要相应权限。生产部署应事先评估权限与迁移方案，不要照搬开发数据库或直接用 root 运行业务。

从 `backend` 启动 `python run.py`。`GET /health` 用于进程健康检查，它不验证地图额度、模型质量或完整业务数据库状态。默认 `DEBUG=false` 不开放交互文档。

## Web

在 `frontend` 执行 `npm ci`，复制 `.env.example` 为 `.env.local`。本地后端一般使用 `http://localhost:8000`。如使用前端地图，填写自己的 Web 地图配置；`VITE_*` 会进入客户端，不能放数据库密码、后端模型 Key 或微信 AppSecret。

执行 `npm run dev`；构建检查执行 `npm run build`。

## 微信小程序

`frontend_miniapp_patch` 是同步补丁，不是完整 HBuilderX 工程。整理导出的 `miniapp/` 包含当前使用的页面、API 和 Vue 3 应用入口，并提供去标识的最小配置，不携带历史编译结果及未使用的模板库。

1. 在 HBuilderX 导入 `miniapp/`，配置自己的 DCloud / 微信 AppID。
2. 在 `miniapp/api/index.js` 填入自己的 HTTPS 后端地址。导出物使用 `https://api.example.com` 占位，不连接原项目生产环境。
3. 在微信后台配置对应合法域名与隐私说明。上线要求以实际后台提示为准。
4. 使用 HBuilderX 编译到微信开发者工具，实际检查登录、生成、时间轴、站点选择和编辑。

当前没有为完整小程序补齐独立 npm/CLI 编译流水线，因此不能声称 `npm install && npm run build` 就能得到小程序发行包。页面逻辑测试也不替代微信编译和真机测试。

## 测试费用和数据

默认运行离线回归不会消耗模型/地图额度。不要在自动化任务中默认加入 `run_real_evaluation.py --execute --confirm-paid`。真实评测产物在 `backend/test-results/`，含行程细节和供应商结果；公开前另做去标识和授权检查，不整目录上传。

当前依赖声明包含版本范围，尚未建立完全锁定的 Python 可复现环境；新环境安装后必须重跑回归，不把本机通过解释成所有未来依赖组合均通过。
