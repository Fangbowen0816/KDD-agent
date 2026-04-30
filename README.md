# DataAgent-Bench Agent 项目（自定义版本）

本项目是一个基于 ReAct 范式的 **数据分析 Agent 框架实现**，用于在结构化数据（CSV / JSON / SQL / 文本）上进行多步推理与自动化分析。

---

# 1. 项目结构

```text
kdd-agent/
├── data/                     # 数据目录（不提交大规模数据）
│   └── public/input/        # 测试任务输入
│
├── configs/                 # 配置文件
│   └── react_baseline.yaml
│
├── src/
│   ├── agent/               # ReAct agent 核心逻辑
│   ├── tools/               # 工具系统（CSV/SQL/Python等）
│   ├── runtime/             # 运行调度（runner）
│   ├── prompt/              # prompt 设计
│   └── utils/
│
├── artifacts/               # 运行结果输出
│   └── runs/
│
├── run.py                  # 主入口（可选）
├── requirements.txt
└── README.md
````

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

## 2.3 配置 API（如 OpenAI-compatible / DeepSeek）

在 `configs/react_baseline.yaml` 中填写：

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

# 4. 数据格式说明

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
* SQLite（数据库）
* TXT（文本）

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

# 6. Agent 工具系统

Agent 通过工具访问数据：

| 工具             | 功能        |
| -------------- | --------- |
| list_context   | 查看文件结构    |
| read_csv       | 读取表格      |
| read_json      | 读取 JSON   |
| read_doc       | 读取文本      |
| execute_python | 执行 Python |
| execute_sql    | 查询 SQLite |
| answer         | 提交最终答案    |

---

# 7. 核心设计思想

本项目基于：

* ReAct（Reasoning + Acting）
* Tool-augmented LLM
* 多步数据分析 agent
* 可复现 benchmark pipeline

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
* tool 调用失败

---

# 10. 项目定位

本项目用于：

* KDD 类数据智能任务
* LLM agent 系统实验
* 多工具数据推理 benchmark

---