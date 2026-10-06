# 刀具消耗演示系统（toolwear）

机加工团队从**工序记录**估计刀具消耗，而不是只按安装天数换刀。

- **React**：机床刀位看板、加工区段时间线、负载与消耗区间展示
- **FastAPI + NumPy**：按给定经验模型（演示参数）向量化计算区段负载并累计
- **PostgreSQL**：保存刀具、刃口、装刀区间、切削时间与工况

> ⚠ **模型限定为演示参数**，不连接真实机床，也不替代安全换刀决策。
> 剩余寿命只以**消耗区间**（低/中/高/达上限）表达，**不是精确失效倒计时**。

## 经验模型（演示参数，未标定）

每个切削区段独立计算负载，再按发生时间累计：

```
load_i = t_i[min] · (rpm_i / 8000)^4 · k_material · k_interrupted
```

| 参数 | 演示值 |
|---|---|
| 参考转速 | 8000 rpm |
| 速度指数 | 4（Taylor 类经验指数） |
| 断续切削冲击系数 | 1.25 |
| 材料系数 | 铝 0.6 / 钢 1.0 / 不锈钢 1.5 / 钛 2.2 |

核心规则：

- **空转、暂停与切削分开**：空转/暂停负载恒为 0，时长单独统计。
- **转速或材料变化必须切段**，逐段计算后累计（模型按区段数组向量化计算）。
- **缺失工况的切削段负载为未知（NULL）**，绝不当零；汇总时单独列出并标记估计不完整。
- **刀具转机床保留身份与历史**：转机床 = 结束当前装刀区间 + 开新区间，`tool_id` 不变。
- **乱序事件**：区段按发生时间归属到装刀区间，与到达顺序无关；`id` 顺序保留到达顺序供审计。
- **负载可回溯**：总量 = 逐区段已知负载之和，可按工序号 / 材料 / 机床 / 刃口分组核对。

## 目录

```
backend/
  app/
    load_model.py   # NumPy 经验负载模型（演示参数）
    models.py       # SQLAlchemy：Machine / Tool / ToolEdge / Assignment / Segment
    services.py     # 装刀、转机床、区段归属、负载报告、消耗区间
    api.py          # REST 路由
    seed.py         # 三个演示案例
  tests/            # pytest：模型、乱序、转移、回溯、区间估计
frontend/
  src/components/   # MachineBoard / ToolList / ToolDetail
scripts/dev.sh      # 一键启动
```

## 运行

```bash
# 依赖：Python 3.11（fastapi/numpy/sqlalchemy/psycopg2）、Node 20、PostgreSQL 16
# 本环境 PostgreSQL 以独立二进制运行在 localhost:5432（scripts/dev.sh 会自动拉起）

bash scripts/dev.sh
# 前端 http://localhost:5173   后端 http://localhost:8000/docs
```

首次进入页面点击「重建演示案例」灌入演示数据（或 `POST /api/seed/demo`）。

```bash
cd backend && python3 -m pytest tests/ -q   # 14 个测试
```

## 演示案例

1. **断续切削** — T-1001 在 MC-01#1：12 段 3 分钟短切削（断续冲击系数），段间空转。
2. **事件乱序** — T-1002 在 MC-01#2：区段按打乱顺序上报，累计结果与顺序上报一致。
3. **同刀不同材料** — T-1003：MC-01#3 加工铝、钢 → 转 MC-02#1 加工钛；
   其中一段切削**缺少转速与材料**，负载记为未知（不计零），估计标记为不完整。

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/machines` | 机床刀位看板 |
| GET | `/api/tools` | 刀具列表（位置、已知负载、消耗区间） |
| GET | `/api/tools/{id}` | 刃口与装刀历史 |
| POST | `/api/tools/{id}/mount` `/unmount` `/transfer` | 装刀 / 卸刀 / 转机床 |
| POST | `/api/segments:batch` | 批量接收区段（乱序安全） |
| GET | `/api/tools/{id}/load` | 负载报告：逐段明细 + 工序/材料/机床/刃口回溯 + 未知区段 |
| GET | `/api/tools/{id}/life-estimate` | 消耗区间估计（定性，非倒计时） |
| GET | `/api/model-info` | 模型参数与免责声明 |
