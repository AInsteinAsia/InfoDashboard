# InfoDashboard

用自然语言描述需求，自动生成并部署 Streamlit 数据看板。

```
用户输入 → 分析师（探索DB结构）→ 开发工程师（生成代码）→ QA（静态检查）→ 部署工程师（Docker）→ 看板URL
```

## 技术栈

- **后端**: FastAPI + SSE 流式输出
- **AI流水线**: LangGraph（4节点 agentic pipeline）
- **LLM**: Claude / OpenAI-compatible APIs（DeepSeek、Qwen 等）
- **数据库**: SQL Server（通过 frp SOCKS5 隧道访问内网）
- **部署**: Docker SDK，自动构建镜像并启动容器

## 快速开始

**依赖**: Python ≥ 3.10，Docker Desktop 运行中

```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 配置环境变量
cp .env.example .env.local
# 编辑 .env.local，填写 LLM API Key 和数据库信息

# 3. 配置 frp 隧道（用于访问内网数据库）
cp tools/frpc-visitor.ini.example tools/frpc-visitor.ini
# 编辑 tools/frpc-visitor.ini，填写 frp 服务端地址、token 和 sk

# 4. 启动服务（frp 隧道由服务自动启动）
python main.py
# 或: uvicorn main:app --reload
```

访问 http://localhost:8001 打开聊天界面。

## 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `ANTHROPIC_API_KEY` | Anthropic API Key | `sk-ant-...` |
| `OPENAI_API_KEY` | OpenAI 兼容 API Key | `sk-...` |
| `OPENAI_BASE_URL` | 自定义 API 地址（DeepSeek 等）| `https://api.deepseek.com` |
| `DEFAULT_MODEL` | 默认模型 | `claude-sonnet-4-6` |
| `DB_HOST` | SQL Server 地址 | `192.168.2.5` |
| `DB_PORT` | SQL Server 端口 | `1433` |
| `DB_USER` / `DB_PASS` / `DB_NAME` | 数据库认证 | |
| `SOCKS5_HOST/PORT/USER/PASS` | SOCKS5 代理配置 | `127.0.0.1:1080` |
| `DASHBOARD_PORT_START/END` | 生成看板端口范围 | `8501`–`8600` |

完整示例见 [.env.example](.env.example)。

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/generate` | SSE 流式生成并部署看板 |
| `GET` | `/api/dashboards` | 列出运行中的看板容器 |
| `DELETE` | `/api/dashboards/{id}` | 停止并移除指定看板 |

`POST /api/generate` 请求体：

```json
{
  "user_request": "生成一个显示每日产量和合格率趋势的看板",
  "max_retries": 2
}
```

## 项目结构

```
main.py                  # FastAPI 入口
orchestration/
  graph.py               # LangGraph 流水线定义
  state.py               # 状态类型与 SSE 事件类型
  _llm.py                # LLM 工具函数
  nodes/
    analyst.py           # 探索DB结构，生成需求规格
    developer.py         # 生成 Streamlit 代码
    qa.py                # 静态安全检查（语法/SQL注入/危险代码）
    deployer.py          # Docker 构建与容器管理
tools/
  database.py            # SQL Server SOCKS5 访问
  docker_manager.py      # Docker SDK 封装
  frpc-visitor.ini       # frp 隧道配置
generated/               # 生成的看板代码（运行时创建）
frontend/index.html      # 聊天界面（纯HTML）
```

## 运行测试

```bash
pytest tests/ -v
```
