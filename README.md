# Amazon Q to Claude API Proxy

将 Claude API 请求转换为 Amazon Q/CodeWhisperer 请求的代理服务。

## 功能特性

- ✅ 完整的 Claude API 兼容接口
- ✅ **Web 界面账号管理**（新增）
- ✅ 多账号支持和快速切换
- ✅ 自动 Token 刷新机制
- ✅ SSE 流式响应支持
- ✅ 请求/响应格式自动转换
- ✅ 完善的错误处理和日志

## 架构说明

### 请求流程
```
Claude API 请求 → main.py → converter.py → Amazon Q API
                     ↓
                 auth.py (Token 管理)
                     ↓
                 config.py (读取 account.json)
                     ↓
Amazon Q Event Stream → event_stream_parser.py → parser.py → stream_handler_new.py → Claude SSE 响应
```

### 核心模块

- **main.py** - FastAPI 服务器,处理 API 端点和账号管理
- **account_manager.py** - 账号管理模块（新增）
- **config.py** - 配置管理，从 account.json 读取账号信息
- **auth.py** - Token 自动刷新机制
- **converter.py** - 请求格式转换 (Claude → Amazon Q)
- **event_stream_parser.py** - 解析 AWS Event Stream 二进制格式
- **parser.py** - 事件类型转换 (Amazon Q → Claude)
- **stream_handler_new.py** - 流式响应处理和事件生成
- **message_processor.py** - 历史消息合并,确保 user-assistant 交替
- **models.py** - 数据结构定义

## 快速开始

### 方式一：Docker 部署（推荐）

```bash
# 1. 克隆项目
git clone <your-repo-url>
cd amq2api

# 2. 启动服务
docker compose up -d

# 3. 访问管理界面
# 浏览器打开 http://localhost:3015
```

### 方式二：本地运行

```bash
# 1. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务
python3 main.py

# 4. 访问管理界面
# 浏览器打开 http://localhost:3015
```

## 账号管理

### 通过 Web 界面管理（推荐）

1. **访问管理页面**
   - 打开浏览器访问: `http://localhost:3015/`
   - 或访问: `http://localhost:3015/admin`

2. **添加账号**
   - 点击 "➕ 添加账号" 按钮
   - 填写账号信息：
     - **账号名称**（可选）：便于识别的名称
     - **Refresh Token**（必填）：Amazon Q 刷新令牌
     - **Client ID**（必填）：客户端 ID
     - **Client Secret**（必填）：客户端密钥
     - **Profile ARN**（可选）：仅组织账号需要
   - 点击 "添加账号" 完成

3. **切换账号**
   - 在账号列表中点击要使用的账号
   - 点击 "切换使用" 按钮
   - 当前使用的账号会显示 "✓ 当前使用" 标记

4. **删除账号**
   - 点击账号后的 "删除" 按钮
   - 确认删除操作

### 通过 account.json 管理

账号数据保存在 `account.json` 文件中，也可以直接编辑此文件：

```json
[
  {
    "id": "account-uuid",
    "refresh_token": "your_refresh_token",
    "client_id": "your_client_id",
    "client_secret": "your_client_secret",
    "profile_arn": "",
    "name": "我的主账号",
    "is_active": true,
    "created_at": "2024-01-01T00:00:00"
  }
]
```

**注意**: 
- `is_active: true` 表示当前使用的账号
- 同一时间只能有一个账号为激活状态
- 修改后刷新前端页面即可生效

### 环境变量配置（可选）

如果不使用账号管理功能，仍可通过环境变量配置（向后兼容）：

```bash
# .env 文件
AMAZONQ_REFRESH_TOKEN=your_refresh_token
AMAZONQ_CLIENT_ID=your_client_id
AMAZONQ_CLIENT_SECRET=your_client_secret
AMAZONQ_PROFILE_ARN=  # 可选
PORT=3015
```

## API 接口

### Claude API 兼容接口

#### POST /v1/messages

创建消息（Claude API 兼容）

**请求体：**

```json
{
  "model": "claude-sonnet-4.5",
  "messages": [
    {
      "role": "user",
      "content": "你好"
    }
  ],
  "max_tokens": 4096,
  "temperature": 0.7,
  "stream": true,
  "system": "你是一个有帮助的助手"
}
```

**响应：** 流式 SSE 响应，格式与 Claude API 完全兼容。

#### GET /v1/models

列出可用的模型

**响应：**

```json
{
  "object": "list",
  "data": [
    {
      "id": "claude-sonnet-4",
      "object": "model",
      "created": 1731158400,
      "owned_by": "anthropic",
      "display_name": "Claude Sonnet 4",
      "description": "Claude Sonnet 4 - 高性能 AI 模型"
    },
    {
      "id": "claude-sonnet-4.5",
      "object": "model",
      "created": 1731158400,
      "owned_by": "anthropic",
      "display_name": "Claude Sonnet 4.5",
      "description": "Claude Sonnet 4.5 - 最新旗舰 AI 模型"
    }
  ]
}
```

### 账号管理 API

#### GET /api/accounts

获取所有账号列表（隐藏敏感信息）

**响应：**
```json
{
  "success": true,
  "data": [
    {
      "id": "account-uuid",
      "name": "我的账号",
      "is_active": true,
      "refresh_token": "aorAAAAAGm...",
      "client_id": "_Vsvwl5Xa_...",
      "client_secret": "***",
      "profile_arn": "",
      "created_at": "2024-01-01T00:00:00"
    }
  ]
}
```

#### POST /api/accounts

添加新账号

**请求体：**
```json
{
  "name": "我的账号",
  "refresh_token": "your_refresh_token",
  "client_id": "your_client_id",
  "client_secret": "your_client_secret",
  "profile_arn": ""
}
```

#### POST /api/accounts/{account_id}/activate

激活指定账号（切换账号）

#### DELETE /api/accounts/{account_id}

删除指定账号

### 其他接口

#### GET /health

健康检查端点

**响应：**
```json
{
  "status": "healthy",
  "has_token": true,
  "token_expired": false
}
```

## 测试服务

```bash
# 健康检查
curl http://localhost:3015/health

# 获取模型列表
curl http://localhost:3015/v1/models

# 发送消息
curl -X POST http://localhost:3015/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4.5",
    "messages": [
      {
        "role": "user",
        "content": "Hello, how are you?"
      }
    ],
    "max_tokens": 1024,
    "stream": true
  }'
```

## 配置说明

### 环境变量

| 变量名 | 必需 | 默认值 | 说明 |
|--------|------|--------|------|
| `AMAZONQ_REFRESH_TOKEN` | ❌ | - | Amazon Q 刷新令牌（可通过 Web 界面管理）|
| `AMAZONQ_CLIENT_ID` | ❌ | - | 客户端 ID（可通过 Web 界面管理）|
| `AMAZONQ_CLIENT_SECRET` | ❌ | - | 客户端密钥（可通过 Web 界面管理）|
| `AMAZONQ_PROFILE_ARN` | ❌ | 空 | Profile ARN（组织账号）|
| `PORT` | ❌ | 3015 | 服务监听端口 |
| `AMAZONQ_API_ENDPOINT` | ❌ | https://q.us-east-1.amazonaws.com/ | API 端点 |
| `AMAZONQ_TOKEN_ENDPOINT` | ❌ | https://oidc.us-east-1.amazonaws.com/token | Token 端点 |

## 项目结构

```
amq2api/
├── static/                    # 前端静态文件（新增）
│   └── index.html            # 账号管理界面
├── account.json              # 账号数据文件（新增）
├── account_manager.py        # 账号管理模块（新增）
├── config.py                 # 配置管理
├── auth.py                   # 认证模块
├── models.py                 # 数据结构
├── converter.py              # 请求转换
├── parser.py                 # 事件解析
├── event_stream_parser.py    # Event Stream 解析
├── stream_handler_new.py     # 流处理
├── message_processor.py      # 消息处理
├── main.py                   # 主服务
├── requirements.txt          # Python 依赖
├── Dockerfile                # Docker 镜像
├── docker-compose.yml        # Docker Compose 配置
├── start.sh                  # 启动脚本
└── README.md                 # 使用说明
```

## Docker 部署

### 使用 Docker Compose

```bash
# 启动服务
docker compose up -d

# 查看日志
docker compose logs -f

# 停止服务
docker compose down

# 重新构建
docker compose build --no-cache
```

### 数据持久化

Docker 部署时会自动挂载：
- `account.json` - 账号数据文件
- Token 缓存目录

数据会保存在宿主机，重启容器不会丢失。

## 工作流程

```
用户浏览器
    ↓
    ↓ 访问 Web 界面管理账号
    ↓
静态文件服务 (static/index.html)
    ↓
    ↓ 调用账号管理 API
    ↓
账号管理器 (account_manager.py)
    ↓
    ↓ 读写 account.json
    ↓
配置管理 (config.py)
    ↓
    ↓ 获取激活账号
    ↓
Claude API 请求
    ↓
代理服务 (main.py)
    ↓
    ├─→ 认证 (auth.py)
    │   └─→ 刷新 Token
    ↓
    ├─→ 转换请求 (converter.py)
    ↓
    ├─→ 发送到 Amazon Q API
    ↓
    ├─→ 解析响应流
    ↓
    └─→ 返回 Claude 格式响应
```

## 注意事项

### 账号管理

1. **账号切换**
   - 切换账号后会自动重新加载配置
   - 无需重启服务

2. **数据安全**
   - `account.json` 包含敏感信息，请妥善保管
   - 已添加到 `.gitignore`，不会提交到 Git

3. **账号激活**
   - 第一个添加的账号会自动激活
   - 删除激活账号会自动切换到第一个账号
   - 如果所有账号都未激活，会使用第一个账号

### Token 管理

1. **自动刷新**
   - access_token 会自动刷新
   - 提前 5 分钟刷新以避免过期
   - refresh_token 如果更新会自动保存

2. **Token 缓存**
   - Token 缓存在 `~/.amazonq_token_cache.json`
   - 重启服务后仍然有效

### 其他

1. **流式响应**
   - 当前仅支持流式响应（stream=true）
   - 非流式响应暂未实现

2. **Token 计数**
   - 使用简化的 token 计数（约 4 字符 = 1 token）
   - 建议集成 Anthropic 官方 tokenizer 以获得准确计数

3. **错误处理**
   - 所有错误都会记录到日志
   - HTTP 错误会返回适当的状态码
   - 上游 API 错误会透传给客户端

## 故障排查

### 问题：无法访问账号管理页面

**解决方案：**
- 确保 `static` 目录存在且包含 `index.html`
- 如使用 Docker，确保重新构建了镜像
- 查看服务日志是否有错误

### 问题：账号添加失败

**解决方案：**
- 检查 Refresh Token、Client ID、Client Secret 是否正确
- 查看浏览器控制台和服务端日志
- 确保 `account.json` 文件有写入权限

### 问题：Token 刷新失败

**解决方案：**
- 检查当前激活账号的配置是否正确
- 在 Web 界面查看当前使用的账号
- 尝试切换到其他账号测试

### 问题：上游 API 返回错误

**解决方案：**
- 检查网络连接
- 验证 Amazon Q 账号是否有效
- 查看日志中的详细错误信息

### 问题：流式响应中断

**解决方案：**
- 检查网络稳定性
- 增加超时时间（在 `main.py` 中调整 `timeout` 参数）
- 查看日志中的错误信息

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！

## 更新日志

### v2.0.0 (最新)
- ✨ 新增 Web 界面账号管理
- ✨ 支持多账号管理和快速切换
- ✨ 账号数据保存在 `account.json`
- ✨ 新增 `/v1/models` 接口
- ✨ 改进 Docker 部署配置
- 🐛 修复若干已知问题

### v1.0.0
- 🎉 初始版本发布
- ✅ Claude API 代理功能
- ✅ 自动 Token 刷新
- ✅ 流式响应支持
