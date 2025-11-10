"""
主服务模块
FastAPI 服务器，提供 Claude API 兼容的接口
"""
import logging
import httpx
import threading
import time
import uuid
import json
from typing import Optional, Generator
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from pydantic import BaseModel

from config import read_global_config, get_config_sync, reset_global_config
from auth import get_auth_headers, refresh_token
from models import ClaudeRequest, ClaudeMessage
from converter import convert_claude_to_codewhisperer_request, codewhisperer_request_to_dict
from stream_handler_new import handle_amazonq_stream
from message_processor import process_claude_history_for_amazonq, log_history_summary
from account_manager import get_account_manager

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ===== 后台定时刷新 Token 线程 =====
def _background_token_refresh():
    """后台线程：定时检查并刷新即将过期的 token"""
    logger.info("后台 token 刷新线程已启动")
    while True:
        try:
            time.sleep(300)  # 每5分钟检查一次
            logger.info("开始检查需要刷新的账号...")
            
            manager = get_account_manager()
            accounts = manager.accounts
            now = datetime.now()
            
            for account in accounts:
                try:
                    # 检查是否需要刷新（25分钟未刷新的账号）
                    should_refresh = False
                    
                    if not account.last_refresh_time:
                        should_refresh = True
                        logger.info(f"账号 {account.name} 从未刷新过，准备刷新")
                    else:
                        last_refresh = datetime.fromisoformat(account.last_refresh_time)
                        time_since_refresh = (now - last_refresh).total_seconds()
                        if time_since_refresh > 1500:  # 25分钟
                            should_refresh = True
                            logger.info(f"账号 {account.name} 已 {time_since_refresh/60:.1f} 分钟未刷新，准备刷新")
                    
                    if should_refresh:
                        # 临时激活该账号进行刷新
                        old_active = None
                        for acc in manager.accounts:
                            if acc.is_active:
                                old_active = acc
                                acc.is_active = False
                        
                        account.is_active = True
                        reset_global_config()
                        
                        # 执行刷新
                        logger.info(f"正在刷新账号: {account.name}")
                        import asyncio
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(refresh_token())
                            manager.update_refresh_status(account.id, "success")
                            logger.info(f"账号 {account.name} 刷新成功")
                        except Exception as e:
                            manager.update_refresh_status(account.id, "failed")
                            logger.error(f"账号 {account.name} 刷新失败: {e}")
                        finally:
                            loop.close()
                        
                        # 恢复原来的激活状态
                        account.is_active = False
                        if old_active:
                            old_active.is_active = True
                        reset_global_config()
                        
                        time.sleep(2)  # 每个账号之间间隔2秒
                        
                except Exception as e:
                    logger.error(f"处理账号 {account.name} 时出错: {e}")
                    continue
            
            logger.info("本轮刷新检查完成")
            
        except Exception as e:
            logger.error(f"后台刷新线程出错: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化配置
    logger.info("正在初始化配置...")
    try:
        await read_global_config()
        logger.info("配置初始化成功")
    except Exception as e:
        logger.error(f"配置初始化失败: {e}")
        raise
    
    # 启动后台刷新线程
    refresh_thread = threading.Thread(target=_background_token_refresh, daemon=True)
    refresh_thread.start()
    logger.info("后台 token 刷新线程已启动")

    yield

    # 关闭时清理资源
    logger.info("正在关闭服务...")


# 创建 FastAPI 应用
app = FastAPI(
    title="Amazon Q to Claude API Proxy",
    description="将 Claude API 请求转换为 Amazon Q/CodeWhisperer 请求的代理服务",
    version="1.0.0",
    lifespan=lifespan
)

# 挂载静态文件目录（如果存在）
import os
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
else:
    logger.warning("static 目录不存在，账号管理前端页面将不可用")


# ===== 账号管理 API 数据模型 =====
class AddAccountRequest(BaseModel):
    refresh_token: str
    client_id: str
    client_secret: str
    profile_arn: str = ""
    name: str = ""


# ===== OpenAI 兼容 API 数据模型 =====
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: Optional[str] = "claude-sonnet-4.5"
    messages: list[ChatMessage]
    stream: Optional[bool] = True
    max_tokens: Optional[int] = 4096
    temperature: Optional[float] = None


@app.get("/")
async def root():
    """返回账号管理前端页面"""
    import os
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    else:
        return {
            "status": "ok",
            "service": "Amazon Q to Claude API Proxy",
            "version": "1.0.0",
            "message": "账号管理前端页面不可用，请访问 /api/accounts 使用 API"
        }


@app.get("/admin")
async def admin():
    """账号管理页面"""
    import os
    if os.path.exists("static/index.html"):
        return FileResponse("static/index.html")
    else:
        raise HTTPException(
            status_code=404,
            detail="账号管理前端页面不可用，请确保 static 目录存在"
        )


@app.get("/health")
async def health():
    """健康检查端点"""
    try:
        config = get_config_sync()
        return {
            "status": "healthy",
            "has_token": config.access_token is not None,
            "token_expired": config.is_token_expired()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


@app.get("/v1/models")
async def list_models():
    """
    列出可用的模型列表
    Claude API 兼容接口
    """
    from datetime import datetime
    
    current_timestamp = int(datetime.now().timestamp())
    
    return {
        "object": "list",
        "data": [
            {
                "id": "claude-sonnet-4",
                "object": "model",
                "created": current_timestamp,
                "owned_by": "anthropic",
                "display_name": "Claude Sonnet 4",
                "description": "Claude Sonnet 4 - 高性能 AI 模型"
            },
            {
                "id": "claude-sonnet-4.5",
                "object": "model",
                "created": current_timestamp,
                "owned_by": "anthropic",
                "display_name": "Claude Sonnet 4.5",
                "description": "Claude Sonnet 4.5 - 最新旗舰 AI 模型"
            }
        ]
    }


# ===== 账号管理 API 端点 =====

@app.get("/api/accounts")
async def get_accounts():
    """获取所有账号列表"""
    try:
        manager = get_account_manager()
        accounts = manager.get_all_accounts()
        return JSONResponse(content={"success": True, "data": accounts})
    except Exception as e:
        logger.error(f"获取账号列表失败: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


@app.post("/api/accounts")
async def add_account(req: AddAccountRequest):
    """添加新账号"""
    try:
        manager = get_account_manager()
        account = manager.add_account(
            refresh_token=req.refresh_token,
            client_id=req.client_id,
            client_secret=req.client_secret,
            profile_arn=req.profile_arn if req.profile_arn else None,
            name=req.name if req.name else None
        )
        
        # 重新加载配置（如果这是第一个账号）
        reset_global_config()
        
        return JSONResponse(content={
            "success": True,
            "message": "账号添加成功",
            "data": {"id": account.id, "name": account.name}
        })
    except Exception as e:
        logger.error(f"添加账号失败: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: str):
    """删除账号"""
    try:
        manager = get_account_manager()
        success = manager.delete_account(account_id)
        
        if not success:
            return JSONResponse(
                content={"success": False, "error": "账号不存在"},
                status_code=404
            )
        
        # 重新加载配置
        reset_global_config()
        
        return JSONResponse(content={"success": True, "message": "账号删除成功"})
    except Exception as e:
        logger.error(f"删除账号失败: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


@app.post("/api/accounts/{account_id}/activate")
async def activate_account(account_id: str):
    """激活指定账号"""
    try:
        manager = get_account_manager()
        success = manager.activate_account(account_id)
        
        if not success:
            return JSONResponse(
                content={"success": False, "error": "账号不存在"},
                status_code=404
            )
        
        # 重新加载配置
        reset_global_config()
        
        return JSONResponse(content={"success": True, "message": "账号切换成功"})
    except Exception as e:
        logger.error(f"激活账号失败: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


@app.post("/v2/accounts/{account_id}/refresh")
async def refresh_account_token(account_id: str):
    """
    手动刷新指定账号的 Token
    参考 amazonq2api 项目实现
    """
    try:
        manager = get_account_manager()
        account = manager.get_account_by_id(account_id)
        
        if not account:
            return JSONResponse(
                content={"success": False, "error": "账号不存在"},
                status_code=404
            )
        
        if not account.refresh_token or not account.client_id or not account.client_secret:
            return JSONResponse(
                content={"success": False, "error": "账号信息不完整，无法刷新"},
                status_code=400
            )
        
        logger.info(f"开始手动刷新账号: {account.name}")
        
        # 临时激活该账号进行刷新
        old_active_id = None
        for acc in manager.accounts:
            if acc.is_active:
                old_active_id = acc.id
                acc.is_active = False
        
        account.is_active = True
        reset_global_config()
        
        try:
            # 执行刷新
            await refresh_token()
            
            # 更新刷新状态
            manager.update_refresh_status(account.id, "success")
            
            # 恢复原来的激活状态
            account.is_active = False
            if old_active_id:
                for acc in manager.accounts:
                    if acc.id == old_active_id:
                        acc.is_active = True
                        break
            elif old_active_id is None:
                account.is_active = True  # 如果原来就是激活的，保持激活
            
            reset_global_config()
            
            logger.info(f"账号 {account.name} 刷新成功")
            
            # 重新加载账号信息
            refreshed_account = manager.get_account_by_id(account_id)
            return JSONResponse(content={
                "success": True,
                "message": "Token 刷新成功",
                "data": {
                    "id": refreshed_account.id,
                    "name": refreshed_account.name,
                    "last_refresh_time": refreshed_account.last_refresh_time,
                    "last_refresh_status": refreshed_account.last_refresh_status
                }
            })
            
        except Exception as e:
            # 刷新失败，更新状态
            manager.update_refresh_status(account.id, "failed")
            
            # 恢复原来的激活状态
            account.is_active = False
            if old_active_id:
                for acc in manager.accounts:
                    if acc.id == old_active_id:
                        acc.is_active = True
                        break
            
            reset_global_config()
            
            logger.error(f"账号 {account.name} 刷新失败: {e}")
            raise
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"刷新账号 Token 失败: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


#  ===== OpenAI 兼容接口 =====

def _openai_sse_format(obj: dict) -> str:
    """OpenAI SSE 格式化"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _openai_non_streaming_response(text: str, model: str) -> dict:
    """OpenAI 非流式响应格式"""
    created = int(time.time())
    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    """
    OpenAI 兼容的聊天接口
    将 OpenAI 格式转换为 Claude 格式，然后调用 Amazon Q
    """
    try:
        # 将 OpenAI 格式的消息转换为 Claude 格式
        claude_messages = []
        system_message = None
        
        for msg in req.messages:
            if msg.role == "system":
                system_message = msg.content
            else:
                claude_messages.append(ClaudeMessage(
                    role=msg.role,
                    content=msg.content
                ))
        
        # 构建 Claude 请求
        claude_req = ClaudeRequest(
            model=req.model or "claude-sonnet-4.5",
            messages=claude_messages,
            max_tokens=req.max_tokens or 4096,
            temperature=req.temperature,
            stream=req.stream if req.stream is not None else True,
            system=system_message
        )
        
        # 获取配置和认证
        config = await read_global_config()
        codewhisperer_req = convert_claude_to_codewhisperer_request(
            claude_req,
            conversation_id=None,
            profile_arn=config.profile_arn
        )
        
        codewhisperer_dict = codewhisperer_request_to_dict(codewhisperer_req)
        model = claude_req.model
        
        # 获取认证头
        base_auth_headers = await get_auth_headers()
        
        # 构建请求头
        auth_headers = {
            **base_auth_headers,
            "Content-Type": "application/x-amz-json-1.0",
            "X-Amz-Target": "AmazonCodeWhispererStreamingService.GenerateAssistantResponse",
            "User-Agent": "aws-sdk-rust/1.3.9 ua/2.1 api/codewhispererstreaming/0.1.11582 os/macos lang/rust/1.87.0 md/appVersion-1.19.3 app/AmazonQ-For-CLI",
            "X-Amz-User-Agent": "aws-sdk-rust/1.3.9 ua/2.1 api/codewhispererstreaming/0.1.11582 os/macos lang/rust/1.87.0 m/F app/AmazonQ-For-CLI",
            "X-Amzn-Codewhisperer-Optout": "true",
            "Amz-Sdk-Request": "attempt=1; max=3",
            "Amz-Sdk-Invocation-Id": str(uuid.uuid4()),
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br"
        }
        
        api_url = config.api_endpoint.rstrip('/')
        
        # 判断是否流式响应
        do_stream = req.stream if req.stream is not None else True
        
        if not do_stream:
            # 非流式响应
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    api_url,
                    json=codewhisperer_dict,
                    headers=auth_headers
                )
                
                if response.status_code != 200:
                    error_text = await response.aread()
                    logger.error(f"上游 API 错误: {response.status_code} {error_text}")
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"上游 API 错误: {error_text.decode()}"
                    )
                
                # 收集完整响应
                full_text = ""
                async for chunk in response.aiter_bytes():
                    if chunk:
                        # 解析事件流获取文本内容
                        # 这里简化处理，实际应该解析 event stream
                        pass
                
                return JSONResponse(content=_openai_non_streaming_response(full_text, model))
        
        else:
            # 流式响应 - 转换为 OpenAI 格式
            created = int(time.time())
            stream_id = f"chatcmpl-{uuid.uuid4()}"
            
            async def byte_stream():
                async with httpx.AsyncClient(timeout=300.0) as client:
                    async with client.stream(
                        "POST",
                        api_url,
                        json=codewhisperer_dict,
                        headers=auth_headers
                    ) as response:
                        if response.status_code != 200:
                            error_text = await response.aread()
                            logger.error(f"上游 API 错误: {response.status_code} {error_text}")
                            raise HTTPException(
                                status_code=response.status_code,
                                detail=f"上游 API 错误: {error_text.decode()}"
                            )
                        
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                yield chunk
            
            async def openai_stream():
                """将 Amazon Q 流转换为 OpenAI 格式"""
                role_sent = False
                
                # 处理内容流
                async for event_block in handle_amazonq_stream(byte_stream(), model=model, request_data=req.model_dump()):
                    # event_block 是完整的 SSE 事件块，格式：
                    # event: content_block_delta\n
                    # data: {...}\n
                    # \n
                    
                    # 按行分割
                    lines = event_block.split('\n')
                    event_type_line = None
                    data_line = None
                    
                    for line in lines:
                        if line.startswith("event: "):
                            event_type_line = line[7:].strip()
                        elif line.startswith("data: "):
                            data_line = line[6:].strip()
                    
                    # 如果没有数据行，跳过
                    if not data_line:
                        continue
                    
                    try:
                        event_data = json.loads(data_line)
                        event_type = event_data.get("type")
                        
                        logger.info(f"[OpenAI] 处理事件类型: {event_type}")
                        
                        # 第一次发送 role
                        if not role_sent:
                            openai_event = _openai_sse_format({
                                "id": stream_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                            })
                            logger.info(f"[OpenAI] 发送 role 事件")
                            yield openai_event
                            role_sent = True
                        
                        # 提取文本内容
                        if event_type == "content_block_delta":
                            delta = event_data.get("delta", {})
                            text = delta.get("text", "")
                            if text:
                                openai_event = _openai_sse_format({
                                    "id": stream_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model,
                                    "choices": [{
                                        "index": 0,
                                        "delta": {"content": text},
                                        "finish_reason": None
                                    }],
                                })
                                logger.info(f"[OpenAI] 发送文本块: {text[:50]}")
                                yield openai_event
                        
                        elif event_type == "message_stop":
                            # 发送结束标记
                            openai_event = _openai_sse_format({
                                "id": stream_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": "stop"
                                }],
                            })
                            logger.info(f"[OpenAI] 发送结束事件")
                            yield openai_event
                    
                    except json.JSONDecodeError as e:
                        logger.warning(f"JSON 解析失败: {data_line[:100] if data_line else 'empty'}, error: {e}")
                        continue
                
                # 发送 [DONE]
                logger.info(f"[OpenAI] 发送 [DONE]")
                yield "data: [DONE]\n\n"
            
            return StreamingResponse(
                openai_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OpenAI 聊天接口错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")


@app.post("/v2/accounts/refresh-all")
async def refresh_all_accounts():
    """
    一键刷新所有账号的 Token
    """
    try:
        manager = get_account_manager()
        accounts = manager.accounts
        
        if not accounts:
            return JSONResponse(
                content={"success": False, "error": "没有可刷新的账号"},
                status_code=400
            )
        
        logger.info(f"开始批量刷新 {len(accounts)} 个账号")
        
        # 记录原来的激活账号
        old_active_id = None
        for acc in accounts:
            if acc.is_active:
                old_active_id = acc.id
                break
        
        success_count = 0
        failed_count = 0
        results = []
        
        for account in accounts:
            try:
                if not account.refresh_token or not account.client_id or not account.client_secret:
                    logger.warning(f"账号 {account.name} 信息不完整，跳过")
                    failed_count += 1
                    results.append({
                        "id": account.id,
                        "name": account.name,
                        "status": "skipped",
                        "error": "账号信息不完整"
                    })
                    continue
                
                # 临时激活该账号
                for acc in accounts:
                    acc.is_active = False
                account.is_active = True
                reset_global_config()
                
                logger.info(f"正在刷新账号: {account.name}")
                
                try:
                    # 执行刷新
                    await refresh_token()
                    manager.update_refresh_status(account.id, "success")
                    success_count += 1
                    results.append({
                        "id": account.id,
                        "name": account.name,
                        "status": "success"
                    })
                    logger.info(f"账号 {account.name} 刷新成功")
                except Exception as e:
                    manager.update_refresh_status(account.id, "failed")
                    failed_count += 1
                    results.append({
                        "id": account.id,
                        "name": account.name,
                        "status": "failed",
                        "error": str(e)
                    })
                    logger.error(f"账号 {account.name} 刷新失败: {e}")
                
            except Exception as e:
                logger.error(f"处理账号 {account.name} 时出错: {e}")
                failed_count += 1
                results.append({
                    "id": account.id,
                    "name": account.name,
                    "status": "error",
                    "error": str(e)
                })
        
        # 恢复原来的激活状态
        for acc in accounts:
            acc.is_active = False
        if old_active_id:
            for acc in accounts:
                if acc.id == old_active_id:
                    acc.is_active = True
                    break
        elif accounts:
            accounts[0].is_active = True
        
        reset_global_config()
        
        logger.info(f"批量刷新完成: 成功 {success_count}/{len(accounts)}, 失败 {failed_count}")
        
        return JSONResponse(content={
            "success": True,
            "message": f"批量刷新完成",
            "data": {
                "total": len(accounts),
                "success_count": success_count,
                "failed_count": failed_count,
                "results": results
            }
        })
        
    except Exception as e:
        logger.error(f"批量刷新失败: {e}")
        return JSONResponse(
            content={"success": False, "error": str(e)},
            status_code=500
        )


@app.post("/v1/messages")
async def create_message(request: Request):
    """
    Claude API 兼容的消息创建端点
    接收 Claude 格式的请求，转换为 CodeWhisperer 格式并返回流式响应
    """
    try:
        # 解析请求体
        request_data = await request.json()

        # 标准 Claude API 格式 - 转换为 conversationState
        logger.info(f"收到标准 Claude API 请求: {request_data.get('model', 'unknown')}")

        # 转换为 ClaudeRequest 对象
        claude_req = parse_claude_request(request_data)

        # 获取配置
        config = await read_global_config()

        # 转换为 CodeWhisperer 请求
        codewhisperer_req = convert_claude_to_codewhisperer_request(
            claude_req,
            conversation_id=None,  # 自动生成
            profile_arn=config.profile_arn
        )

        # 转换为字典
        codewhisperer_dict = codewhisperer_request_to_dict(codewhisperer_req)
        model = claude_req.model

        # 处理历史记录：合并连续的 userInputMessage
        conversation_state = codewhisperer_dict.get("conversationState", {})
        history = conversation_state.get("history", [])

        if history:
            # 记录原始历史记录
            logger.info("=" * 80)
            logger.info("原始历史记录:")
            log_history_summary(history, prefix="[原始] ")

            # 合并连续的用户消息
            processed_history = process_claude_history_for_amazonq(history)

            # 记录处理后的历史记录
            logger.info("=" * 80)
            logger.info("处理后的历史记录:")
            log_history_summary(processed_history, prefix="[处理后] ")

            # 更新请求体
            conversation_state["history"] = processed_history
            codewhisperer_dict["conversationState"] = conversation_state

        # 处理 currentMessage 中的重复 toolResults（标准 Claude API 格式）
        conversation_state = codewhisperer_dict.get("conversationState", {})
        current_message = conversation_state.get("currentMessage", {})
        user_input_message = current_message.get("userInputMessage", {})
        user_input_message_context = user_input_message.get("userInputMessageContext", {})

        # 合并 currentMessage 中重复的 toolResults
        tool_results = user_input_message_context.get("toolResults", [])
        if tool_results:
            merged_tool_results = []
            seen_tool_use_ids = set()

            for result in tool_results:
                tool_use_id = result.get("toolUseId")
                if tool_use_id in seen_tool_use_ids:
                    # 找到已存在的条目，合并 content
                    for existing in merged_tool_results:
                        if existing.get("toolUseId") == tool_use_id:
                            existing["content"].extend(result.get("content", []))
                            logger.info(f"[CURRENT MESSAGE - CLAUDE API] 合并重复的 toolUseId {tool_use_id} 的 content")
                            break
                else:
                    # 新条目
                    seen_tool_use_ids.add(tool_use_id)
                    merged_tool_results.append(result)

            user_input_message_context["toolResults"] = merged_tool_results
            user_input_message["userInputMessageContext"] = user_input_message_context
            current_message["userInputMessage"] = user_input_message
            conversation_state["currentMessage"] = current_message
            codewhisperer_dict["conversationState"] = conversation_state

        final_request = codewhisperer_dict

        # 调试：打印请求体
        import json
        logger.info(f"转换后的请求体: {json.dumps(final_request, indent=2, ensure_ascii=False)}")

        # 获取认证头
        base_auth_headers = await get_auth_headers()

        # 构建 Amazon Q 特定的请求头（完整版本）
        import uuid
        auth_headers = {
            **base_auth_headers,
            "Content-Type": "application/x-amz-json-1.0",
            "X-Amz-Target": "AmazonCodeWhispererStreamingService.GenerateAssistantResponse",
            "User-Agent": "aws-sdk-rust/1.3.9 ua/2.1 api/codewhispererstreaming/0.1.11582 os/macos lang/rust/1.87.0 md/appVersion-1.19.3 app/AmazonQ-For-CLI",
            "X-Amz-User-Agent": "aws-sdk-rust/1.3.9 ua/2.1 api/codewhispererstreaming/0.1.11582 os/macos lang/rust/1.87.0 m/F app/AmazonQ-For-CLI",
            "X-Amzn-Codewhisperer-Optout": "true",
            "Amz-Sdk-Request": "attempt=1; max=3",
            "Amz-Sdk-Invocation-Id": str(uuid.uuid4()),
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br"
        }

        # 发送请求到 Amazon Q
        logger.info("正在发送请求到 Amazon Q...")

        # API URL（根路径，不需要额外路径）
        config = await read_global_config()
        api_url = config.api_endpoint.rstrip('/')

        # 创建字节流响应
        async def byte_stream():
            async with httpx.AsyncClient(timeout=300.0) as client:
                try:
                    async with client.stream(
                        "POST",
                        api_url,
                        json=final_request,
                        headers=auth_headers
                    ) as response:
                        # 检查响应状态
                        if response.status_code != 200:
                            error_text = await response.aread()
                            logger.error(f"上游 API 错误: {response.status_code} {error_text}")
                            raise HTTPException(
                                status_code=response.status_code,
                                detail=f"上游 API 错误: {error_text.decode()}"
                            )

                        # 处理 Event Stream（字节流）
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                yield chunk

                except httpx.RequestError as e:
                    logger.error(f"请求错误: {e}")
                    raise HTTPException(status_code=502, detail=f"上游服务错误: {str(e)}")

        # 返回流式响应
        async def claude_stream():
            async for event in handle_amazonq_stream(byte_stream(), model=model, request_data=request_data):
                yield event

        return StreamingResponse(
            claude_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理请求时发生错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")


def parse_claude_request(data: dict) -> ClaudeRequest:
    """
    解析 Claude API 请求数据

    Args:
        data: 请求数据字典

    Returns:
        ClaudeRequest: Claude 请求对象
    """
    from models import ClaudeMessage, ClaudeTool

    # 解析消息
    messages = []
    for msg in data.get("messages", []):
        messages.append(ClaudeMessage(
            role=msg["role"],
            content=msg["content"]
        ))

    # 解析工具
    tools = None
    if "tools" in data:
        tools = []
        for tool in data["tools"]:
            tools.append(ClaudeTool(
                name=tool["name"],
                description=tool["description"],
                input_schema=tool["input_schema"]
            ))

    return ClaudeRequest(
        model=data.get("model", "claude-sonnet-4.5"),
        messages=messages,
        max_tokens=data.get("max_tokens", 4096),
        temperature=data.get("temperature"),
        tools=tools,
        stream=data.get("stream", True),
        system=data.get("system")
    )


if __name__ == "__main__":
    import uvicorn

    # 读取配置
    try:
        import asyncio
        config = asyncio.run(read_global_config())
        port = config.port
    except Exception as e:
        logger.error(f"无法读取配置: {e}")
        port = 3015

    logger.info(f"正在启动服务，监听端口 {port}...")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
    )
