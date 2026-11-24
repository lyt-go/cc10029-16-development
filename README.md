# 跨系统数据交换与批量传输平台

面向异构系统间批量数据交换的字段契约、增量窗口抽取、暂存落地、校验和对账、断点续传与到达核对平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/views/CredentialView.vue     批次归属凭据核对工作台
│   ├── src/data/modules.ts              业务模块配置
│   ├── src/domain/<模块>.ts             承载真实业务差异的前端领域逻辑
│   ├── src/api/              统一请求封装与凭据专用接口
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/routers/ownership.py 批次归属凭据专用接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/scope_range.py  交付范围区间运算
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 源端登记 | `source_register` | 源端系统 | 源端编号、系统名称、数据域 |
| 目标端登记 | `target_register` | 目标端点 | 目标编号、系统名称、接收格式 |
| 字段契约 | `field_contract` | 字段映射 | 契约编号、源字段、目标字段 |
| 增量窗口 | `extract_window` | 抽取窗口 | 窗口编号、源端编号、增量字段 |
| 传输批次 | `transfer_batch` | 传输批次 | 批次编号、窗口编号、目标编号 |
| 暂存落地 | `staging_area` | 暂存分区 | 暂存编号、批次编号、落地路径 |
| 校验核对 | `checksum_verify` | 校验任务 | 校验编号、批次编号、源端校验值 |
| 到达对账 | `arrival_reconcile` | 对账单 | 对账编号、批次编号、应到条数 |
| 字段冲突 | `conflict_resolve` | 冲突记录 | 冲突编号、记录主键、冲突字段 |
| 断点续传 | `resume_point` | 续传断点 | 断点编号、批次编号、已完成分片 |
| 速率整形 | `speed_control` | 限速策略 | 策略编号、作用范围、每秒记录数 |
| 拒收回执 | `reject_record` | 拒收记录 | 拒收编号、批次编号、记录主键 |
| 格式适配 | `format_adapt` | 适配版本 | 适配编号、记录格式、旧版结构 |
| 传输血缘 | `transfer_lineage` | 血缘链路 | 链路编号、源端编号、抽取窗口 |
| 批次归档 | `history_archive` | 归档卷 | 归档编号、批次范围、归档路径 |
| 历史重放 | `exchange_replay` | 重放任务 | 重放编号、归档编号、重放范围 |
| 时效监控 | `sla_monitor` | 时效指标 | 指标编号、源端编号、约定到达时限 |
| 通道配置 | `channel_config` | 传输通道 | 通道编号、传输协议、并发连接数 |

## 批次数据归属凭据

源端与目标端之间原本只有批次号和条数，缺少一份能说明“这份批次数据归属于哪个源端、
覆盖哪些记录、目标端实际领用了多少”的凭据。`ownership_credential` 模块补上这一环：

- **配置快照校验**：签发时从源端/目标端登记、抽取窗口与批次固化一份配置快照摘要，
  `GET /api/ownership_credential/{id}/verify-scope` 核对交付范围是否落在快照允许的记录数内。
- **领用范围区分**：交付范围拆成「已领用范围」和「未领用范围」，
  `POST /api/ownership_credential/{id}/claim` 只划走要发运的部分；传输批次
  （`TransferBatchService.start_transfer`）按凭据已领用范围筛选记录，未领用部分留在源端。
- **归属反查**：`GET /api/ownership_credential/resolve?target_code=…&record_range=…`
  按目标端接收序号回答每条记录属于哪个源端/凭据，两个来源同时认领同一序号会标记重叠。
- **交接**：`POST /api/ownership_credential/{id}/handover` 只追加交接留痕，
  不替新接收方撤销旧凭据；`保留到结案` 让旧凭据继续有效，`发货前重新确认` 会把旧凭据
  推进到待重新确认、并让传输闸门拦下旧批发运，直到 `确认发运`。已落地/已结案旧批的
  交付范围、领用范围与阶段不因交接改写。
- **落地核对与历史回查**：`GET /{id}/verify-landed` 用暂存落地记录的 `target_no`
  核对缺失与越界序号；`GET /history` 默认返回已落地/已结案凭据，历史 A 批仍可回查。

前端 `批次归属凭据` 页面（`/ownership_credential`）提供范围条、回执解读、
领用/交接弹窗与目标端归属核对面板。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 批次归属凭据的子路径接口（`/api/ownership_credential/issue` 等）在通用资源路由之前注册，
  避免被 `/api/{module}/{entry_id}` 抢先匹配。

领域：数据工程
子方向：数仓与数据湖
