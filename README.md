# work_life_agent

工作生活 Agent 核心源码，包含个人记录、用户鉴权、云手机分配、MUA 操作和 Web 前端。

客户首次部署请阅读 [CUSTOMER_DEPLOYMENT_GUIDE.md](CUSTOMER_DEPLOYMENT_GUIDE.md)。
完整方案、架构流程和生产化建议请阅读
[docs/WORK_LIFE_AGENT_ARCHITECTURE.md](docs/WORK_LIFE_AGENT_ARCHITECTURE.md)。

## 目录

- `main.py`：HTTP API 和 Agent 服务入口。
- `auth.py`：OIDC JWT 验签和用户身份解析。
- `assistant/`：Agent、业务工具及用户到云手机的绑定逻辑。
- `assistant/cloud_phone/`：云手机任务、实例管理、应用安装和拉流客户端。
- `frontend/`：登录、对话和云手机拉流页面。
- `config/`：云手机池与用户绑定的数据结构示例。
- `smoke_test.py`：核心工具导入与本地存储冒烟测试。

## 本地配置

```bash
cp .env.example .env
cp config/pod_pool.example.json config/pod_pool.json
```

填写 `.env` 中的模型、OIDC 和云手机配置，再导入当前终端：

```bash
set -a
source .env
set +a
python main.py
```

独立运行 MUA 路由命令：

```bash
python -m scripts.auto_mua_agent "打开抖音并搜索指定内容"
```

部署清单、构建产物、调试日志及具体云资源标识不包含在此目录中。
