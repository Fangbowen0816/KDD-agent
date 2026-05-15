# DataAgent-Bench Agent 项目（自定义版本）

本项目是一个基于阶段式 ReAct / NL2SQL 范式的 **数据分析 Agent 基线实现**，用于在结构化数据（CSV / JSON / SQLite）和辅助文本说明上进行多步推理与自动化分析。

---

# 1. 项目结构

```text
agent-project/
├── data/
│   └── public/input/              # DABench 公共任务输入
├── configs/                       # 配置文件
│   └── react_baseline*.yaml
├── src/data_agent_baseline/
│   ├── agents/                    # agent 主流程、prompt、runtime
│   ├── benchmark/                 # task / dataset 定义
│   ├── run/                       # runner、批量执行、输出落盘
│   ├── tools/                     # DataEngine 与工具注册
│   └── config.py                  # 配置加载
├── artifacts/
│   └── runs/                      # 每次运行的 trace 与 prediction
├── compare.py                     # 结果对比脚本
├── pyproject.toml
└── README.md
```

---

# 2. 环境搭建

## 2.1 安装 Python 环境

建议 Python >= 3.10

```bash
python -V
```

---

## 2.2 安装依赖（推荐 uv）

```bash
pip install uv
uv sync
```

或传统方式：

```bash
pip install -r requirements.txt
```

---

## 2.3 配置 API（OpenAI-compatible）

在 `configs/react_baseline.yaml` 或本地变体配置中填写：

```yaml
agent:
  model: your-model-name
  api_base: your-api-base
  api_key: your-api-key
  max_steps: 16
  temperature: 0.0
```

---

# 3. CLI 使用方式

统一入口：

```bash
uv run dabench <command> --config <config_path>
```

---

## 3.1 查看环境状态

```bash
uv run dabench status --config configs/react_baseline.yaml
```

功能：

* 检查数据路径
* 检查配置是否正确
* 输出任务数量

---

## 3.2 运行单个任务

```bash
uv run dabench run-task task_1 --config configs/react_baseline.yaml
```

输出：

```text
artifacts/runs/<run_id>/task_1/
├── trace.json
└── prediction.csv
```

---

## 3.3 运行完整 benchmark

```bash
uv run dabench run-benchmark --config configs/react_baseline.yaml
```

可选参数：

```bash
--limit 10   # 只跑前10个任务（调试用）
```

---

## 3.4 查看任务信息

```bash
uv run dabench inspect-task task_1 --config configs/react_baseline.yaml
```

功能：

* 查看 question
* 查看 context 文件结构
* 检查数据类型

---

# 4. 数据与加载方式

## 4.1 输入结构

```text
data/public/input/task_<id>/
├── task.json
└── context/
```

---

## 4.2 task.json 示例

```json
{
  "task_id": "task_1",
  "difficulty": "medium",
  "question": "计算某指标的统计结果"
}
```

---

## 4.3 context 数据类型

* CSV（表格数据）
* JSON（结构化数据）
* SQLite / DB（数据库）
* Markdown 文本（`knowledge.md` 与 `context/doc/*.md`）

当前 DataEngine 加载规则：

* CSV：直接注册为 DuckDB view
* JSON：普通 JSON 直接加载；若是 `{table, records}` 包裹结构，会自动展开 `records`
* SQLite：将库中的每张原表注册为一个 DuckDB view
* 暴露给 LLM 的表名就是 DuckDB 中实际可查询的表名

---

# 5. 输出结构

## 单任务输出

```text
artifacts/runs/<run_id>/<task_id>/
├── trace.json       # agent 推理轨迹
└── prediction.csv   # 最终答案
```

---

## 全局输出

```text
artifacts/runs/<run_id>/summary.json
```

包含：

* 总任务数
* 成功率
* 评分统计

---

# 6. Agent 工作流与工具

当前 agent 采用固定阶段流程：

```text
load_data -> catalog -> plan -> plan2sql -> answer
```

各阶段含义：

* `load_data`：递归加载 `context/` 下的 csv / json / db / sqlite 到 DataEngine
* `catalog`：读取 `task.json`、`knowledge.md`、schema 和 sample rows，生成字段语义目录
* `plan`：读取 task、catalog、schema、`context/doc/*.md`，生成查询计划
* `plan2sql`：根据 plan 和 schema 生成或修复最终 SQL
* `answer`：将 SQL 结果转成最终答案表

当前工具系统已收敛为围绕 DataEngine 的最小集合：

| 工具             | 功能        |
| -------------- | --------- |
| inspect_data_catalog | 查看 DataEngine schema 与生成的 catalog |
| execute_dataengine_sql | 对已加载到 DuckDB 的表执行只读 SQL |
| answer | 提交最终答案 |

---

# 7. 核心设计思想

本项目基于：

* 阶段式 ReAct / plan-first NL2SQL
* Tool-augmented LLM
* DataEngine 统一多数据源查询层
* 可复现 benchmark pipeline
* trace 驱动的问题定位与回归分析

---

# 8. 注意事项

* ❌ 不要提交 data/ 原始数据
* ❌ 不要泄露 API Key
* ✔ 推荐使用 feature branch 开发
* ✔ 所有实验结果保存在 artifacts/

---

# 9. 常见问题

## Q1：运行失败找不到数据？

检查：

```bash
uv run dabench status
```

---

## Q2：API 报错？

检查 config：

* api_base 是否正确
* api_key 是否有效

---

## Q3：结果为空？

可能原因：

* max_steps 太小
* prompt 不稳定
* 数据源未正确加载
* SQL 规划或执行失败

---

## Q4：为什么 trace 里 schema 和 knowledge 描述不一致？

当前约束是：

* `knowledge.md` 只用于补充真实 schema 的语义说明
* 不允许用 `knowledge.md` 构造 schema 中不存在的字段
* 实际可查询字段始终以 DataEngine `describe_schema()` 为准

---

# 10. 项目定位

本项目当前定位为：

* DABench 结构化数据问答 baseline
* 多源数据统一加载与 SQL 推理实验框架
* 面向协作开发的可追踪 agent pipeline

---
