# Pipi PPT

独立网站实现，基于 Presenton `2b5078ba266c67b16bc4f97943081e46d6dc0f21`。上游代码、LICENSE、NOTICE 保留。生产入口位于 `pipi/`，复用上游六套 v2 模板的布局、文本槽位与颜色；Pipi 的账号、生成调度、场景编辑器和 PptxGenJS 导出器独立实现。未向公网暴露上游全局供应商配置、外部登录或未经许可确认的二进制导出包。

## 运行

1. 部署配套 `new-api` 的应用授权改动；主站为 `https://pipixia1.online`，PPT 域名规划为 `https://ppt.pipixia1.online`。设置 `PIPI_PPT_ENABLED=true`、`PIPI_PPT_REDIRECT_URI=https://ppt.pipixia1.online/api/auth/callback`，保留消费日志。DNS 和证书需在部署平台实际配置。
2. 通过部署平台的机密变量配置 `pipi/.env.example` 所列字段。Fernet 密钥必须为 32 字节随机密钥的 URL-safe base64 编码。数据库密码使用 URL-safe 随机字符。不要把密钥提交到 Git，不要使用普通 API Key 代替授权流程。
3. 在仓库根目录执行 `docker compose -f pipi/compose.yaml up -d --build`。通过 HTTPS 反向代理把域名转发到 `127.0.0.1:3100`。反向代理允许 22 MB 请求体，超时 240 秒；禁止记录 `/api/auth/callback` 查询参数和 Cookie/Authorization。
4. 用 pipiapi 管理员账户登录，在「站点管理」设置经过验证的文字/视觉模型及图片模型，再开启新任务。网站所有模型请求只连接固定的 `PIPI_GATEWAY_URL`。
5. 普通用户直接通过 pipiapi 登录授权，使用自身余额或适用套餐。授权在 pipiapi 个人设置撤销。

主站目前使用 Caddy；对应维护入口与正式代理示例位于 `ops/Caddyfile.ppt-maintenance.example`、`ops/Caddyfile.ppt.example`。部署前先确认服务器余量；当前主站可用内存不足以按默认部署栈直接启动所有服务，资源与实际验收状态见 [验证记录](VERIFICATION.md)。

当前试运行使用 `compose.low.yaml`，把生成和导出各限制为一个进程。启动命令为 `docker compose --env-file .env -f compose.yaml -f compose.low.yaml up -d`；扩大并发前先观察内存、转换耗时和失败恢复。

开发模式：Python 3.12 `pip install -r pipi/requirements.lock`；设置必需环境变量和 PostgreSQL/Redis；`uvicorn pipi.backend.api:app --port 8000 --no-access-log`。在 `pipi/web` 执行 `bun install --frozen-lockfile && bun run dev`，另启动 Compose 中对应的三个 Celery 命令。Redis 仅保存任务 ID，任务参数和进度以 PostgreSQL 为准。SQLite 仅用于自动化测试。

## 产品范围

- 六套内置模板来自 Presenton：executive、modern、momentum、general、nova、signal。
- 上传 PPTX，使用 python-pptx 读取文字、图片、主要版式、表格和单系列图表；LibreOffice 生成原始预览，视觉模型分析风格。确认转换后布局才进入个人模板库。
- 支持主题、粘贴大纲和 PDF/DOCX/Markdown/TXT；可编辑大纲、页面文字、图片、表格、图表、形状、位置和尺寸；增删排序、AI 重写和演讲备注。
- PPTX 使用原生文本、形状、表格和图表；PDF 由同一 PPTX 转换。普通编辑和导出不调用模型。
- 生成任务逐页保存，幂等键去重，支持取消及安全恢复。模型调用在发送前写入步骤记录；超时或 Worker 丢失无法确认消费时进入待确认，禁止自动重发。
- 文件、模板、任务与作品均按用户隔离，凭据只以 Fernet 密文保存；浏览器持有 HttpOnly 会话 Cookie。变更接口校验 Origin。

当前模板转换有明确边界：统一为 16:9，字体替换为 Noto Sans CJK SC（在预览中显示）；复杂矢量路径简化为基本形状，分组、SmartArt、复杂动画和完整母版效果不保证保真。嵌入宏/二进制对象、外部关系和超限压缩包拒绝导入。图片模型必须支持 OpenAI 兼容接口并返回 `b64_json`；任意外部图片 URL 不下载。

## 测试

```text
python -m pytest pipi/tests -q
python -m ruff check pipi/backend pipi/tests
cd pipi/web
bun run typecheck
bun run lint
bun run build
bun run test:e2e
```

自动测试使用测试网关，验证多用户隔离、权限撤销、上传限制、生成检查点、超时与重复投递、实际 PPTX 文本结构。真实模型能力、生产 PostgreSQL/Redis、LibreOffice 转换和 PowerPoint/WPS 视觉效果必须在目标部署环境验收，不能由模拟测试替代。

`.github/workflows/pipi-ci.yml` 提供带 PostgreSQL、Redis、LibreOffice 的集成测试配置。本地缺少这些服务时，对应两个测试明确跳过。可通过 `PIPI_TEST_DATABASE_URL` 和 `PIPI_TEST_REDIS_URL` 指定专用测试服务；PostgreSQL 测试只创建和清理随机测试 schema。不要使用生产服务运行集成测试。浏览器测试会保存页面截图及一份模拟模型内容的示例 PPTX 到 `output/playwright/`。

最新本地验证结果与未完成的上线验收见 [验证记录](VERIFICATION.md)。

## 上线与回滚

详见 [运维与验收](OPERATIONS.md)。上线前保持新任务关闭；出现异常关闭新任务入口，保留数据库和素材卷。不要使用 `docker compose down -v`。回滚到上一个镜像标签时不删授权或消费关联数据。上游 baseline 保持可追溯，不自动追踪其主分支。
