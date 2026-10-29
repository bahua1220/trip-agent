# 许可核对记录

核对日期：2026-10-29。本页记录源码来源与本次补齐文件的依据；日常使用先看 [README](../README.md) 与 [第三方说明](../THIRD_PARTY_NOTICES.md)。

## 1. 原始旅行项目

最早的本地 Git 提交为 `4a3a9739b6c3ec9933d1ae8e53ea18af67f8238e`（2026-07-13）。该提交中旅行项目的 README 已声明 CC BY-NC-SA 4.0，当前 Web 的“来源与许可”也保留了 Datawhale 第 13 章旅行示例的链接及改编说明。

为避免只依据上游最新分支，使用本地初始提交之前最后一次修改该示例的上游提交 `12d19487db091770dbcc13a7580c4897b0ca7270`（2026-04-12）作为核对基准：

- [原示例 README](https://github.com/datawhalechina/hello-agents/blob/12d19487db091770dbcc13a7580c4897b0ca7270/code/chapter13/helloagents-trip-planner/README.md) 明确声明 CC BY-NC-SA 4.0。
- [同版本根目录 LICENSE.txt](https://github.com/datawhalechina/hello-agents/blob/12d19487db091770dbcc13a7580c4897b0ca7270/LICENSE.txt) 为协议全文。
- 已检查 `code/`、`code/chapter13/` 及示例目录，未发现该路径下另行覆盖的 LICENSE / COPYING / NOTICE。
- 该示例有 34 个文件；本地初始旅行目录有 38 个受跟踪文件，其中 17 个文件与上游对应文件 Git blob SHA 完全一致，包括 README、Web 入口与部分配置/服务文件。

这足以确认项目存在原示例代码的沿用，并非仅调用一个框架。该提交是可复核基准，不声称它就是当时下载代码的精确提交。项目主体继续沿用原有 CC BY-NC-SA 4.0 声明，协议全文放在根目录 [LICENSE](../LICENSE)，来源与主要修改写入 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

## 2. 小程序入口模板

`miniapp/index.html` 与 DCloud hello-uniapp 的入口文件正文一致，仅换行格式不同。核对基准为 `ab80c34681013d844be2a9feb362bf96ffeca3ee`：

- [入口文件](https://github.com/dcloudio/hello-uniapp/blob/ab80c34681013d844be2a9feb362bf96ffeca3ee/index.html)。
- [上游 MIT 许可证](https://github.com/dcloudio/hello-uniapp/blob/ab80c34681013d844be2a9feb362bf96ffeca3ee/LICENSE)，保留 `Copyright (c) 2018 DCloud`。
- 原文副本：[LICENSES/DCloud-MIT.txt](../LICENSES/DCloud-MIT.txt)。

MIT 模板部分保留其许可，不用根目录 CC 声明覆盖它。原 HBuilderX 项目里的模板 package 元数据不代表旅行项目整体采用 MIT；未用到的示例和静态第三方库没有进入源码副本。

## 3. 依赖检查范围

Python：检查了 requirements 中 17 个直接依赖的本机已安装发行包元数据及可用的随包许可。版本范围没有因此锁定；以后安装的版本仍需查看其实际许可。本次未将整个本机 Python 环境及其依赖打包发布，也未把本机全局环境当作项目的完整传递依赖锁文件。

Web：检查当前 package-lock v3 的 174 个包条目（包含平台可选项），全部具有 license 字段；这不是实际安装包数量。13 个直接/开发依赖中 TypeScript 为 Apache-2.0，其余为 MIT。传递依赖的许可表达式如下：

| 许可表达式 | 锁文件条目数 |
| --- | ---: |
| MIT | 166 |
| (MPL-2.0 OR Apache-2.0) | 1 |
| BSD-2-Clause | 1 |
| ISC | 2 |
| (MIT AND Zlib) | 1 |
| MIT OR SEE LICENSE IN FEEL-FREE.md | 1 |
| BSD-3-Clause | 1 |
| Apache-2.0 | 1 |

其中多许可条目分别包括 DOMPurify、pako、rgbcolor；表达式按锁文件原样记录，没有把 OR 选项改写成 AND，也没有用项目许可替代依赖许可。分发包含依赖的构建产物时，应再检查实际打包内容并保留相应 LICENSE / NOTICE。

## 4. 本次处理范围

新增主体许可全文、DCloud 模板许可副本、来源与改动说明；README 只保留简短许可入口。导出工具同时携带这些文件，避免后续归集时遗漏许可。

本次整理是基于上述来源和文件的发布准备，不扩大上游的商业使用许可，也不处理供应商地图数据、商标、用户数据或服务账号的权利。CC 对软件的使用建议见 [官方 FAQ](https://creativecommons.org/faq/#can-i-apply-a-creative-commons-license-to-software)；本项目此处沿用已有上游条款，不将其擅自换成其他软件许可证。
