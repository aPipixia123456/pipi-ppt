# 交付与验证记录

日期：2026-10-09。所有生成流程测试使用模拟模型网关；没有进行付费模型调用或推送代码。PPT 应用与主站授权补丁已按低并发配置部署，生产生成入口尚未做真实模型验收。用户已添加 DNS，主站 Caddy 已切换到 PPT 应用入口。

## 代码位置与域名

- PPT 独立 Git 仓库：`G:\pipi-ppt`，分支 `codex/pipi-ppt`，Presenton 基线 `2b5078ba266c67b16bc4f97943081e46d6dc0f21`。
- 网站入口在 `pipi/`。复用 Presenton 十六套模板、槽位与素材；为适配逐用户凭据和持久队列，生成调用、场景编辑器及导出器单独实现。原有 LICENSE、NOTICE 未修改。
- 主站授权补丁位于 `G:\newapi`，协议说明为 `docs/pipi-ppt.md`。主站原本存在大量其他未提交修改，本次没有打包或提交这些内容；部署应独立审查授权相关变更及其依赖。
- 主站：`https://pipixia1.online`；PPT 域名：`https://ppt.pipixia1.online`；准确回调：`https://ppt.pipixia1.online/api/auth/callback`。DNS 和服务器证书已检查，应用上线情况见下方。

## 域名接入检查

- Google 与 Cloudflare 的公开 DNS 查询均返回 A 记录 `154.9.232.22`，与主站一致；本机代理 DNS 的 `198.18.*` 地址不作为源站地址。
- 通过已有 `pipixia1` SSH 配置检查主站。Caddy 版本为 `2.11.4`；在现有配置中导入独立的 `pipi-ppt.caddy`，只显示维护提示，不启动 PPT 应用或授权请求。维护与正式代理两个示例均通过目标 Caddy 的配置验证，然后热加载维护配置。
- 服务器上 HTTP 返回 `308` 跳转到 HTTPS；HTTPS 使用正常 CA 校验和主机名验证，证书包含 `ppt.pipixia1.online`，到期时间 `2027-01-07 03:13:33 UTC`。服务器自测返回 `503` 与“Pipi PPT 正在部署，暂未开放”，响应禁止缓存。
- 变更后从本机检查主站 `/api/status`，返回 `200`、`success=true`。
- 服务器内环回和另一台检查主机均可通过 TLS 访问 PPT；PPT 首页返回 `200`，标题为 `Pipi PPT · 把想法变成演示`，浏览器加载无页面错误。主站 `/api/status` 返回 `200`、`success=true`。
- 主站应用授权补丁已发布，错误授权请求返回 `400 invalid_request`；PPT `/api/auth/start` 可获得跳转到主站 `/app-authorize` 的授权地址。真实用户同意、兑换和模型消费尚未执行。
- 低并发配置已启用：生成 Worker 1 个进程、导出 Worker 1 个进程、单站点服务；部署后服务器约有 4.6 GB 可用内存和 9 GB 磁盘空间。完整并发配置仍需独立资源。

## 已运行检查

| 范围 | 实际结果 |
| --- | --- |
| 主站 `go test ./model ./middleware ./router ./service` | 通过 |
| 主站授权迁移测试 | 临时 SQLite 升级旧 Token 表、重复迁移、授权、去重、撤销通过 |
| 主站消费查询追加回归 | 独立日志库、其他用户隔离、延迟日志、套餐消费与退款汇总通过 |
| 主站前端 typecheck / build | 通过 |
| 主站新增授权页面及脚本定向 lint | 通过；全仓 lint 仍有任务开始前其他修改中的既有错误，不能视为全仓通过 |
| Pipi Python 测试 | **20 passed, 2 skipped**；跳过项为真实 LibreOffice/Poppler 转换及 PostgreSQL/Redis/Celery 集成服务 |
| Pipi Python Ruff | 通过 |
| Pipi Next.js typecheck / ESLint / production build | 通过 |
| Playwright 全流程 | **4 passed**：授权到导出、模板与英文界面、网络重试与编辑保护、管理员模型与模板配置 |
| 浏览器示例导出 | 另行运行下载流程通过，保存到 `output/playwright/pipi-ppt-demo.pptx` |
| PPTX 结构 | python-pptx 重新打开，确认文字、Logo、表格、图表保留独立元素；中文长文与文字对齐检查通过 |
| 部署静态检查 | Compose/CI YAML 解析及备份脚本 Bash 语法通过；没有运行 Docker 构建 |

测试中有一条 Starlette TestClient 对 httpx 的弃用提示，不影响当前结果。

## 已实现的关键边界

- PKCE/state、单次五分钟授权码、三十天授权、撤销与账户状态校验，浏览器只持本站会话 Cookie。
- 模型地址固定；用户模型权限与平台名单取交集，后台任务重新校验，禁止跨用户凭据切换。
- PostgreSQL 持久任务和 Celery 调度；任务与模型步骤两层幂等。超时消费不明的步骤不会自动重发。
- 按用户控制任务、文件和存储；无匿名文件目录；上传包、外部关系、宏、体积和内容边界检查。
- 失败和取消保留完成内容；旧任务恢复不会覆盖暂停后手动修改。已经拒绝或消费不明的模型步骤需要核对后处理。
- 普通编辑和导出不调用模型；完成任务清理模型响应正文，保留请求和消费关联。
- 模板确认、字体替代说明、内置模板开关、模型与并发配置、私网指标、备份和回滚配置。

## 公开上线仍需完成

1. 完成首个真实账户的登录授权和撤销测试，选定文字/视觉及图片模型并验证实际扣费；低并发站点已可作为试运行入口。
2. 选定并真实验证文字/视觉及图片模型；确认两个用户的余额/套餐、实际扣费与 pipiapi 日志一致。
3. 在真实 PostgreSQL/Redis 环境执行队列重启和并发验证，运行 LibreOffice PDF/模板预览。
4. 运行主站 MySQL 5.7 与 PostgreSQL 9.6 授权迁移 CI。工作流已提供，远端执行尚未进行。
5. 在 PowerPoint 和 WPS 检查导出可编辑性、字体、文本溢出与模板位置，然后再打开新任务总开关。

首版模板导入保留主要布局和基础元素；字体统一替代，复杂路径、分组、SmartArt、动画、完整母版效果仍需预览确认，不能承诺像素级还原。图片模型需要支持当前兼容接口的 base64 输出。
