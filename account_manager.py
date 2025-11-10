"""
账号管理模块
负责账号的增删改查和激活切换
"""
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)

# 账号数据文件路径
ACCOUNT_FILE = Path("account.json")


@dataclass
class Account:
    """账号数据模型"""
    id: str
    refresh_token: str
    client_id: str
    client_secret: str
    profile_arn: Optional[str] = None
    name: Optional[str] = None  # 账号别名
    is_active: bool = False
    created_at: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


class AccountManager:
    """账号管理器"""
    
    def __init__(self):
        self.accounts: List[Account] = []
        self.load_accounts()
    
    def load_accounts(self) -> None:
        """从文件加载账号列表"""
        try:
            if ACCOUNT_FILE.exists():
                with open(ACCOUNT_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.accounts = [Account(**acc) for acc in data]
                logger.info(f"成功加载 {len(self.accounts)} 个账号")
            else:
                self.accounts = []
                logger.info("账号文件不存在,初始化为空列表")
        except Exception as e:
            logger.error(f"加载账号文件失败: {e}")
            self.accounts = []
    
    def save_accounts(self) -> None:
        """保存账号列表到文件"""
        try:
            data = [acc.to_dict() for acc in self.accounts]
            with open(ACCOUNT_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"成功保存 {len(self.accounts)} 个账号")
        except Exception as e:
            logger.error(f"保存账号文件失败: {e}")
            raise
    
    def get_all_accounts(self) -> List[Dict[str, Any]]:
        """获取所有账号(隐藏敏感信息)"""
        # 每次获取时重新加载，确保数据是最新的
        self.load_accounts()
        result = []
        for acc in self.accounts:
            acc_dict = acc.to_dict()
            # 隐藏敏感信息,显示前10位...后4位
            if acc_dict['refresh_token']:
                token = acc_dict['refresh_token']
                if len(token) > 14:
                    acc_dict['refresh_token'] = token[:10] + '...' + token[-4:]
                else:
                    acc_dict['refresh_token'] = '***'
            if acc_dict['client_secret']:
                acc_dict['client_secret'] = '***'
            result.append(acc_dict)
        return result
    
    def get_account_by_id(self, account_id: str) -> Optional[Account]:
        """根据ID获取账号"""
        # 每次获取时重新加载，确保数据是最新的
        self.load_accounts()
        for acc in self.accounts:
            if acc.id == account_id:
                return acc
        return None
    
    def get_active_account(self) -> Optional[Account]:
        """获取当前激活的账号"""
        # 每次获取时重新加载，确保数据是最新的
        self.load_accounts()
        for acc in self.accounts:
            if acc.is_active:
                return acc
        # 如果没有激活的账号,返回第一个
        if self.accounts:
            return self.accounts[0]
        return None
    
    def add_account(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        profile_arn: Optional[str] = None,
        name: Optional[str] = None
    ) -> Account:
        """添加新账号"""
        # 生成唯一ID
        import uuid
        account_id = str(uuid.uuid4())
        
        # 如果是第一个账号,自动激活
        is_active = len(self.accounts) == 0
        
        # 创建账号对象
        account = Account(
            id=account_id,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            profile_arn=profile_arn,
            name=name or f"账号 {len(self.accounts) + 1}",
            is_active=is_active,
            created_at=datetime.now().isoformat()
        )
        
        self.accounts.append(account)
        self.save_accounts()
        
        logger.info(f"成功添加账号: {account.name} (ID: {account.id})")
        return account
    
    def delete_account(self, account_id: str) -> bool:
        """删除账号"""
        account = self.get_account_by_id(account_id)
        if not account:
            return False
        
        self.accounts.remove(account)
        
        # 如果删除的是激活账号,激活第一个账号
        if account.is_active and self.accounts:
            self.accounts[0].is_active = True
        
        self.save_accounts()
        logger.info(f"成功删除账号: {account.name} (ID: {account_id})")
        return True
    
    def activate_account(self, account_id: str) -> bool:
        """激活指定账号"""
        account = self.get_account_by_id(account_id)
        if not account:
            return False
        
        # 取消所有账号的激活状态
        for acc in self.accounts:
            acc.is_active = False
        
        # 激活指定账号
        account.is_active = True
        
        self.save_accounts()
        logger.info(f"成功激活账号: {account.name} (ID: {account_id})")
        return True


# 全局账号管理器实例
_account_manager: Optional[AccountManager] = None


def get_account_manager() -> AccountManager:
    """获取账号管理器实例(单例模式)"""
    global _account_manager
    if _account_manager is None:
        _account_manager = AccountManager()
    return _account_manager

