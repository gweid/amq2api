# Amazon Q to Claude API Proxy

将 Claude API 请求转换为 Amazon Q/CodeWhisperer 请求的代理服务。

## 功能特性

- ✅ 完整的 Claude API 兼容接口
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
Amazon Q Event Stream → event_stream_parser.py → parser.py → stream_handler_new.py → Claude SSE 响应
```

### 核心模块

- **main.py** - FastAPI 服务器,处理 `/v1/messages` 端点
- **converter.py** - 请求格式转换 (Claude → Amazon Q)
- **event_stream_parser.py** - 解析 AWS Event Stream 二进制格式
- **parser.py** - 事件类型转换 (Amazon Q → Claude)
- **stream_handler_new.py** - 流式响应处理和事件生成
- **message_processor.py** - 历史消息合并,确保 user-assistant 交替
- **auth.py** - Token 自动刷新机制
- **config.py** - 配置管理和 Token 缓存
- **models.py** - 数据结构定义

## 快速开始

### 1. 安装依赖

```bash
# 创建虚拟环境
python3 -m venv venv

# 激活虚拟环境
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env 文件，填写以下信息：
# - AMAZONQ_REFRESH_TOKEN: 你的 Amazon Q refresh token
# - AMAZONQ_CLIENT_ID: 客户端 ID
# - AMAZONQ_CLIENT_SECRET: 客户端密钥
# - AMAZONQ_PROFILE_ARN: Profile ARN（组织账号需要，个人账号留空）
# - PORT: 服务端口（默认 3015）
```

### 3. 启动服务

```bash
# 使用启动脚本（推荐）
chmod +x start.sh
./start.sh

# 或直接运行
python3 main.py
```

### 4. 测试服务

```bash
# 健康检查
curl http://localhost:3015/health

# 发送测试请求
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
| `AMAZONQ_REFRESH_TOKEN` | ✅ | - | Amazon Q 刷新令牌 |
| `AMAZONQ_CLIENT_ID` | ✅ | - | 客户端 ID |
| `AMAZONQ_CLIENT_SECRET` | ✅ | - | 客户端密钥 |
| `AMAZONQ_PROFILE_ARN` | ❌ | 空 | Profile ARN（组织账号） |
| `PORT` | ❌ | 3015 | 服务监听端口 |
| `AMAZONQ_API_ENDPOINT` | ❌ | https://q.us-east-1.amazonaws.com/ | API 端点 |
| `AMAZONQ_TOKEN_ENDPOINT` | ❌ | https://oidc.us-east-1.amazonaws.com/token | Token 端点 |

## API 接口

### POST /v1/messages

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

**响应：**

流式 SSE 响应，格式与 Claude API 完全兼容。

### GET /health

健康检查端点

**响应：**

```json
{
  "status": "healthy",
  "has_token": true,
  "token_expired": false
}
```

## 工作流程

```
Claude Code 客户端
    ↓
    ↓ Claude API 格式请求
    ↓
代理服务 (main.py)
    ↓
    ├─→ 认证 (auth.py)
    │   └─→ 刷新 Token（如需要）
    ↓
    ├─→ 转换请求 (converter.py)
    │   └─→ Claude 格式 → CodeWhisperer 格式
    ↓
    ├─→ 发送到 Amazon Q API
    ↓
    ├─→ 接收 SSE 流
    ↓
    ├─→ 解析事件 (parser.py)
    │   └─→ CodeWhisperer 事件 → Claude 事件
    ↓
    ├─→ 流处理 (stream_handler.py)
    │   └─→ 累积响应、计算 tokens
    ↓
    └─→ 返回 Claude 格式 SSE 流
        ↓
Claude Code 客户端
```

## 注意事项

1. **Token 管理**
   - access_token 会自动刷新
   - 提前 5 分钟刷新以避免过期
   - refresh_token 如果更新会自动保存

2. **流式响应**
   - 当前仅支持流式响应（stream=true）
   - 非流式响应暂未实现

3. **Token 计数**
   - 使用简化的 token 计数（约 4 字符 = 1 token）
   - 建议集成 Anthropic 官方 tokenizer 以获得准确计数

4. **错误处理**
   - 所有错误都会记录到日志
   - HTTP 错误会返回适当的状态码
   - 上游 API 错误会透传给客户端

## 开发说明

### 项目结构

```
amq2api/
├── .env.example          # 环境变量模板
├── .gitignore           # Git 忽略文件
├── README.md            # 使用说明
├── requirements.txt     # Python 依赖
├── start.sh            # 启动脚本
├── config.py           # 配置管理
├── auth.py             # 认证模块
├── models.py           # 数据结构
├── converter.py        # 请求转换
├── parser.py           # 事件解析
├── stream_handler.py   # 流处理
└── main.py             # 主服务
```

### 扩展功能

如需添加新功能，可以：

1. **添加新的事件类型**
   - 在 `models.py` 中定义新的事件结构
   - 在 `parser.py` 中添加解析逻辑
   - 在 `stream_handler.py` 中添加处理逻辑

2. **支持非流式响应**
   - 在 `main.py` 中实现非流式响应逻辑
   - 累积完整响应后一次性返回

3. **添加缓存**
   - 实现对话历史缓存
   - 减少重复请求

## 故障排查

### 问题：Token 刷新失败

**解决方案：**
- 检查 `AMAZONQ_REFRESH_TOKEN` 是否正确
- 检查 `AMAZONQ_CLIENT_ID` 和 `AMAZONQ_CLIENT_SECRET` 是否正确
- 查看日志中的详细错误信息

### 问题：上游 API 返回错误

**解决方案：**
- 检查 `AMAZONQ_API_ENDPOINT` 是否正确
- 检查网络连接
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
