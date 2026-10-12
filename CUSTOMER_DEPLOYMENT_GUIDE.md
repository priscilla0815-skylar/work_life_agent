# Work Life Agent 部署指南

## 1. 准备

客户需要准备：

- 已登录客户账号的 AgentKit CLI
- 火山 Identity 用户池和 SPA 客户端
- 模型名称及访问凭证
- 火山引擎 AK/SK
- 云手机 `ProductId` 和所属火山引擎账号 ID
- 可用 Pod，或已购买的云手机实例额度与创建 Pod 权限

## 2. 先部署前端

将 `frontend/` 部署到 VeFaaS：

- Runtime：Python 3.12
- 启动命令：`python static_server.py`
- 服务端口：`8000`
- 开启公网 HTTP/API 网关触发器

部署完成后记录前端地址，例如：

```text
https://work-agent.example.com/
```

在 Identity SPA 客户端中添加回调地址：

```text
https://work-agent.example.com/auth.html
```

## 3. 填写配置

```bash
cd work_life_agent
cp .env.example .env
cp config/pod_pool.example.json config/pod_pool.json
```

填写 `.env` 中的空值：

```env
MODEL_AGENT_NAME=
MODEL_AGENT_PROVIDER=
MODEL_AGENT_API_BASE=
MODEL_AGENT_API_KEY=

VOLCENGINE_ACCESS_KEY=
VOLCENGINE_SECRET_KEY=

OIDC_USER_POOL_ID=
OIDC_AUDIENCE=
CORS_ORIGINS=https://work-agent.example.com

VOLC_ACCESSKEY=
VOLC_SECRETKEY=
MUA_PRODUCT_ID=
MUA_ACCOUNT_ID=
```

说明：

- `OIDC_USER_POOL_ID`：Identity 控制台中的用户池 ID。
- `OIDC_AUDIENCE`：Identity SPA 客户端 ID。
- `CORS_ORIGINS`：填写第 2 步得到的前端域名。例如前端地址是 `https://work-agent.example.com/`，这里填写 `https://work-agent.example.com`。
- `MUA_ACCOUNT_ID`：云手机业务所属的火山引擎账号 ID。
- 套餐规格和机房默认自动查询，无需填写。

如需优先分配已有 Pod，编辑 `config/pod_pool.json`；没有已有 Pod 时保持空数组，系统会自动创建。

## 4. 部署 AgentKit Runtime

```bash
cp agentkit.example.yaml agentkit.yaml
```

编辑 `agentkit.yaml`：

1. 将 `common.runtime_envs` 的空值替换为 `.env` 中的对应值。
2. 将 `runtime_jwt_discovery_url` 替换为：

   ```text
   https://<用户池ID>.userpool.auth.id.<区域>.volces.com/.well-known/openid-configuration
   ```

3. 将 `runtime_jwt_allowed_clients` 替换为 SPA 客户端 ID。
4. 新部署保持 `runtime_id: Auto`。

执行部署：

```bash
agentkit launch --config-file agentkit.yaml
```

部署完成后记录 Runtime Endpoint。

## 5. 访问

使用以下格式打开前端：

```text
https://<前端域名>/?endpoint=https://<Runtime-Endpoint>&user_pool_id=<用户池ID>&client_id=<SPA客户端ID>
```

验收：

1. 用户可以完成注册和登录。
2. 登录后可以正常调用 Agent。
3. 用户只能访问自己绑定的云手机。
4. 点击“打开云手机”可以正常拉流。

不要提交 `.env`、`agentkit.yaml`、真实 Pod 清单或用户绑定文件。
