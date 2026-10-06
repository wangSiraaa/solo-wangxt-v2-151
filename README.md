# 刀具消耗估算系统（演示版 / DEMO）

从**工序记录**估计刀具刃口负载，而不是简单按安装天数换刀。

- **React**：展示机床刀位、加工区段、各类负载与跨机床挂载历史。
- **FastAPI + NumPy**：按固定的经验模型（Taylor 风格的简化式）对切段累计负载。
- **PostgreSQL**：保存刀具刃口、材质、切削时间、工况、原始事件、计算区段与溯源关系。

> ⚠ **演示限定**：模型参数为固定演示值，未对接真实机床信号，
> **不能替代安全换刀决策**；"剩余寿命比例"只是基于已知负载的估算，
> **不是精确失效倒计时**。

## 核心规则

1. **空转 / 暂停 / 实际切削分开**：空转单独按很低的速率计；暂停负载为 0；
   只有切削进入主磨损模型。
2. **转速或材料变化必须切段**：模型对时间轴做扫描线切分，
   在每个事件边界切段后分别累计。
3. **缺失工况 ≠ 零**：切削区段若缺转速/进给/切深/材料，或同一时间窗的
   活跃记录互相冲突，该区段 `load = NULL`（未知），单独统计未知时长，
   绝不并入零负载。
4. **刀具身份全局唯一**：刀具从 A 机床拆下、装到 B 机床，`tool_code`、
   刃口材质和全部累计历史保留；`mount_history` 记录每次刀位变更，
   每个计算区段同时记住所属刀具、刀位挂载与机床。
5. **负载可回溯到原工序**：`segment_sources` 多对多记录每个区段由哪些
   原始事件生成，报告里每个区段都带来源事件 id。
6. **事件乱序安全**：区段重建是确定性的全量重算，迟到/乱序的事件入库后
   结果一致。

## 演示案例（`backend/app/seed.py`）

| 刀具 | 案例 |
|---|---|
| T-100 | 钢→不锈钢断续切削（冲击系数 1.8），随后空转、暂停，最后一段缺材料 = 未知负载 |
| T-200 | 事件**乱序入库**（晚到的事件先提交），切段结果仍正确 |
| T-300 | **同一把刀加工多种材料**，之后从 MC-A 转到 MC-B，身份与历史保留 |

## 模型

```
wear_rate = k0 · k_material
          · (rpm / v_ref)^n_v · (feed / f_ref)^n_f · (depth / d_ref)^n_d
load      = wear_rate · duration_min
断续切削  : wear_rate × k_interrupted (=1.80)
空转      : idle_load_per_min · duration （单列）
暂停      : 0
缺失/冲突 : load = NULL（未知，不计零）
```

参数见 `GET /api/model-params` 与前端"演示模型参数"页。

## 快速启动

### 方式一：docker compose（含 PostgreSQL）

```bash
docker compose up --build
# 前端 http://localhost:5173  后端 http://localhost:8000/docs
# API 容器启动时自动建表并 seed 三个演示案例
```

### 方式二：本地开发

```bash
# 后端
cd backend
python -m virtualenv ../.venv          # 若无 pip: python3 get-pip.py --user
../.venv/bin/pip install -r requirements.txt
export DATABASE_URL="postgresql+psycopg2://tooluser:toolpass@localhost:5432/toolload"
../.venv/bin/python -m app.seed        # 建表 + 种子数据（可重复执行）
../.venv/bin/uvicorn app.main:app --reload

# 前端
cd ../frontend
npm install && npm run dev             # http://localhost:5173
```

无 PostgreSQL 时可用 SQLite 做本地验证（生产/正式演示请用 PG）：

```bash
export DATABASE_URL="sqlite:////tmp/demo.db"
../.venv/bin/python -m app.seed
../.venv/bin/uvicorn app.main:app
```

### 测试

```bash
cd backend && ../.venv/bin/python -m pytest -q
# 19 passed：覆盖断续切削、乱序事件、同刀多材料、缺失工况未知、
# 跨机床身份、负载溯源、挂载冲突等
```

## 主要 API

| 方法/路径 | 说明 |
|---|---|
| `GET /api/tools` / `POST /api/tools` | 刀具（刃口类型、材质、累计切削/未知时长） |
| `GET /api/machines` / `POST /api/machines` | 机床与刀位数量 |
| `POST /api/mounts` / `POST /api/dismounts` | 上刀/拆刀（同一刀只能占一个开放刀位） |
| `GET /api/machines/{code}/positions` | 刀位视图：每个刀位当前刀具与负载 |
| `GET /api/tools/{code}/mount-history` | 刀具跨机床挂载轨迹 |
| `POST /api/events` | 提交工序事件（可乱序、可批量、可重叠） |
| `GET /api/tools/{code}/events` | 原始工序事件 |
| `GET /api/tools/{code}/report` | 切段明细、分阶段/分材料负载、未知时长、来源事件 |
| `GET /api/model-params` | 固定演示参数 |

## 数据模型

- `tools`：全局刀具身份（编码、刃口、材质、累计值由区段汇总派生）
- `machines` / `mount_history`：机床、刀位与挂载期间（转机床靠同一 tool_id 的多行记录）
- `process_events`：原始工序区间（cutting/idle/pause + 工况）
- `segments`：切段结果，`load` 可空（NULL = 未知）
- `segment_sources`：区段 ↔ 原始事件多对多溯源
