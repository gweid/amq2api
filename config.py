"""
配置管理模块
负责读取和更新全局配置
"""
import os
import json
import asyncio
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# Token 缓存文件路径
TOKEN_CACHE_FILE = Path.home() / ".amazonq_token_cache.json"


@dataclass
class GlobalConfig:
    """全局配置类"""
    # Amazon Q 配置
    refresh_token: str
    client_id: str
    client_secret: str
    profile_arn: Optional[str] = None

    # API Endpoints
    api_endpoint: str = "https://q.us-east-1.amazonaws.com/"
    token_endpoint: str = "https://oidc.us-east-1.amazonaws.com/token"

    # 服务配置
    port: int = 3015

    # Token 统计配置
    zero_input_token_models: list = field(default_factory=lambda: ["haiku"])

    # 动态更新的 token 信息
    access_token: Optional[str] = None
    token_expires_at: Optional[datetime] = None

    def is_token_expired(self) -> bool:
        """检查 access_token 是否过期"""
        if not self.access_token or not self.token_expires_at:
            return True
        # 提前 5 分钟刷新
        return datetime.now() >= (self.token_expires_at - timedelta(minutes=5))


# 全局配置实例
_global_config: Optional[GlobalConfig] = None
_config_lock = asyncio.Lock()


def _load_token_cache() -> Optional[dict]:
    """从文件加载 token 缓存"""
    try:
        if TOKEN_CACHE_FILE.exists():
            with open(TOKEN_CACHE_FILE, 'r') as f:
                cache = json.load(f)
                # 检查是否过期
                if 'expires_at' in cache:
                    expires_at = datetime.fromisoformat(cache['expires_at'])
                    if datetime.now() < expires_at:
                        return cache
    except Exception as e:
        print(f"加载 token 缓存失败: {e}")
    return None


def _save_token_cache(access_token: str, refresh_token: str, expires_at: datetime) -> None:
    """保存 token 到文件"""
    try:
        cache = {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'expires_at': expires_at.isoformat()
        }
        with open(TOKEN_CACHE_FILE, 'w') as f:
            json.dump(cache, f)
        # 设置文件权限为仅当前用户可读写
        TOKEN_CACHE_FILE.chmod(0o600)
    except Exception as e:
        print(f"保存 token 缓存失败: {e}")


async def read_global_config() -> GlobalConfig:
    """
    读取全局配置（异步安全）
    如果配置未初始化，则从 account.json 或环境变量加载
    """
    global _global_config

    async with _config_lock:
        if _global_config is None:
            # 优先从 account.json 读取账号信息
            from account_manager import get_account_manager
            account_manager = get_account_manager()
            active_account = account_manager.get_active_account()
            
            if active_account:
                # 从账号管理器加载配置
                print(f"从 account.json 加载账号: {active_account.name}")
                zero_token_models = os.getenv("ZERO_INPUT_TOKEN_MODELS", "haiku")
                _global_config = GlobalConfig(
                    refresh_token=active_account.refresh_token,
                    client_id=active_account.client_id,
                    client_secret=active_account.client_secret,
                    profile_arn=active_account.profile_arn,
                    api_endpoint=os.getenv("AMAZONQ_API_ENDPOINT", "https://q.us-east-1.amazonaws.com/"),
                    token_endpoint=os.getenv("AMAZONQ_TOKEN_ENDPOINT", "https://oidc.us-east-1.amazonaws.com/token"),
                    port=int(os.getenv("PORT", "3015")),
                    zero_input_token_models=[m.strip() for m in zero_token_models.split(",")]
                )
            else:
                # 如果 account.json 中没有账号，尝试从环境变量加载（兼容旧版）
                print("account.json 中没有账号，尝试从环境变量加载")
                zero_token_models = os.getenv("ZERO_INPUT_TOKEN_MODELS", "haiku")
                _global_config = GlobalConfig(
                    refresh_token=os.getenv("AMAZONQ_REFRESH_TOKEN", ""),
                    client_id=os.getenv("AMAZONQ_CLIENT_ID", ""),
                    client_secret=os.getenv("AMAZONQ_CLIENT_SECRET", ""),
                    profile_arn=os.getenv("AMAZONQ_PROFILE_ARN") or None,
                    api_endpoint=os.getenv("AMAZONQ_API_ENDPOINT", "https://q.us-east-1.amazonaws.com/"),
                    token_endpoint=os.getenv("AMAZONQ_TOKEN_ENDPOINT", "https://oidc.us-east-1.amazonaws.com/token"),
                    port=int(os.getenv("PORT", "3015")),
                    zero_input_token_models=[m.strip() for m in zero_token_models.split(",")]
                )

            # 验证必需的配置项
            if not _global_config.refresh_token:
                raise ValueError("未找到有效的账号配置，请在前端添加账号或在 .env 中设置 AMAZONQ_REFRESH_TOKEN")
            if not _global_config.client_id:
                raise ValueError("未找到有效的账号配置，请在前端添加账号或在 .env 中设置 AMAZONQ_CLIENT_ID")
            if not _global_config.client_secret:
                raise ValueError("未找到有效的账号配置，请在前端添加账号或在 .env 中设置 AMAZONQ_CLIENT_SECRET")

            # 尝试从缓存加载 token
            cache = _load_token_cache()
            if cache:
                _global_config.access_token = cache.get('access_token')
                _global_config.refresh_token = cache.get('refresh_token', _global_config.refresh_token)
                _global_config.token_expires_at = datetime.fromisoformat(cache['expires_at'])
                print(f"从缓存加载 token，过期时间: {_global_config.token_expires_at}")

        return _global_config


async def update_global_config(
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    expires_in: Optional[int] = None
) -> None:
    """
    更新全局配置（异步安全）

    Args:
        access_token: 新的访问令牌
        refresh_token: 新的刷新令牌
        expires_in: 令牌过期时间（秒）
    """
    global _global_config

    async with _config_lock:
        if _global_config is None:
            await read_global_config()

        if access_token is not None:
            _global_config.access_token = access_token

        if refresh_token is not None:
            _global_config.refresh_token = refresh_token

        if expires_in is not None:
            _global_config.token_expires_at = datetime.now() + timedelta(seconds=expires_in)

        # 保存到缓存文件
        if _global_config.access_token and _global_config.token_expires_at:
            _save_token_cache(
                _global_config.access_token,
                _global_config.refresh_token,
                _global_config.token_expires_at
            )
            print(f"Token 已保存到缓存文件: {TOKEN_CACHE_FILE}")


def get_config_sync() -> GlobalConfig:
    """
    同步获取配置（仅用于非异步上下文）
    注意：如果配置未初始化，会抛出异常
    """
    if _global_config is None:
        raise RuntimeError("配置未初始化，请先调用 read_global_config()")
    return _global_config


def reset_global_config() -> None:
    """
    重置全局配置缓存
    用于账号切换时强制重新加载配置
    """
    global _global_config
    _global_config = None
    print("全局配置已重置")