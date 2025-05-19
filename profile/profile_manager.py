# profile_manager.py
import hashlib
from typing import List, Dict, Any, Optional

# 从 stubs 导入
from stubs.mock_config import global_config
from stubs.mock_dependencies import get_logger, person_info_manager, relationship_manager, MockDatabase

# 导入 SQLite 数据库处理器
from profile.profile_db import ProfileDB


logger = get_logger("ProfileManager")

class ProfileManager:
    """
    管理用户画像信息（profile_info）。
    在过渡阶段，profile_info 的文档ID (_id) 基于 person_info_pid 生成，
    并且主要迁移绰号相关功能。
    此版本适配 SQLite。
    """

    def __init__(self, db_path: Optional[str] = None, sobriquet_db_instance: Optional[ProfileDB] = None):
        # 使用 stubs 中的配置
        self.db_path = db_path or global_config.profile.db_path
        
        if sobriquet_db_instance:
            self.db_handler = sobriquet_db_instance
        else:
            self.db_handler = ProfileDB(self.db_path) # ProfileManager 使用 SobriquetDB 来访问 profile_info

        # self.profile_collection 模拟 MongoDB collection 对象，通过 MockDatabase 适配 SobriquetDB
        # 这使得 get_users_group_sobriquets_for_prompt_injection_data 中的 .find() 调用可以工作
        self.mock_db_interface = MockDatabase(self.db_handler)
        self.profile_collection = self.mock_db_interface.profile_info


        self.profile_id_salt = global_config.security.profile_id_salt
        if self.profile_id_salt == "default_salt_please_change_me" or self.profile_id_salt == "test_salt_for_profile_id": # 后者是我们 stub 里的
            logger.warning(f"安全警告：正在使用测试/默认的 profile_id_salt ('{self.profile_id_salt}')。请在生产配置中设置一个强盐值！")

        if not self.db_handler.is_available(): # SobriquetDB.is_available()
            logger.error(
                f"未能初始化数据库处理器 (路径: '{self.db_path}')。"
                "ProfileManager 的功能将受限。"
            )
        else:
            logger.info(
                f"ProfileManager 初始化成功，使用数据库: '{self.db_path}'"
            )

    def is_available(self) -> bool:
        """检查数据库处理器是否可用。"""
        return self.db_handler is not None and self.db_handler.is_available()

    def generate_profile_document_id(self, person_info_pid: str) -> str:
        """
        为 profile_info 文档生成 _id，基于 person_info_pid 的加盐哈希。
        """
        if not person_info_pid:
            logger.error("生成 profile_document_id 时，person_info_pid 为空。")
            raise ValueError("person_info_pid cannot be empty for ID generation.")
            
        salted_input = f"{self.profile_id_salt}-{person_info_pid}"
        hashed_id = hashlib.sha256(salted_input.encode('utf-8')).hexdigest()
        return hashed_id

    async def get_users_group_sobriquets_for_prompt_injection_data(
        self, platform: str, platform_user_ids: List[str], group_id: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        批量获取多个用户在指定群组的绰号信息，用于 Prompt 注入。
        数据源：profile_info 表 (SQLite, 通过 SobriquetDB 访问), 
                 person_info_manager 和 relationship_manager (模拟的)。
        """
        if not self.is_available():
            logger.error("ProfileManager: 数据库处理器不可用，无法获取绰号数据。")
            return {}
        if not platform_user_ids or not group_id:
            logger.debug("ProfileManager: platform_user_ids 或 group_id 为空，返回空字典。")
            return {}

        user_ids_str = [str(uid) for uid in platform_user_ids]
        group_id_str = str(group_id)
        
        profile_doc_ids_to_query: List[str] = []
        profile_doc_id_to_original_uid_map: Dict[str, str] = {}

        for uid_str in user_ids_str:
            # 使用模拟的 person_info_manager
            person_info_pid = person_info_manager.get_person_id(platform, uid_str)
            if person_info_pid:
                try:
                    profile_doc_id = self.generate_profile_document_id(person_info_pid)
                    if profile_doc_id not in profile_doc_ids_to_query:
                        profile_doc_ids_to_query.append(profile_doc_id)
                    profile_doc_id_to_original_uid_map[profile_doc_id] = uid_str
                except ValueError as e:
                    logger.error(f"为 person_info_pid '{person_info_pid}' (来自 uid '{uid_str}') 生成 profile_doc_id 失败: {e}")
            # else:
                # logger.debug(f"ProfileManager: 未能为平台用户 '{uid_str}' (平台 '{platform}') 获取 person_info_pid。")
        
        if not profile_doc_ids_to_query:
            logger.debug(f"ProfileManager: 未能为提供的 platform_user_ids 生成任何有效的 profile_document_id。")
            return {}

        try:
            # 使用模拟的 relationship_manager
            person_names_map: Dict[str, str] = await relationship_manager.get_person_names_batch(platform, user_ids_str)
        except Exception as e:
            logger.error(f"ProfileManager: 调用 (模拟的) get_person_names_batch 时出错: {e}", exc_info=True)
            person_names_map = {}

        sobriquets_result_map: Dict[str, Dict[str, Any]] = {}
        group_key_in_db = f"{platform}-{group_id_str}" # 例如 "qq-group123"

        try:
            # 使用 self.profile_collection.find (它现在是 MockDbCollection 的一个实例，内部调用 SobriquetDB)
            # 构建 projection
            projection_for_find = {
                "_id": 1,
                f"sobriquets_by_group.{group_key_in_db}.sobriquets": 1 
            }
            
            # profile_cursor 是一个列表，因为我们的 mock find 返回列表
            profile_docs_cursor: List[Dict] = await self.profile_collection.find(
                {"_id": {"$in": profile_doc_ids_to_query}},
                projection=projection_for_find
            )
            
            for profile_doc in profile_docs_cursor: # Iterating over the list of documents
                profile_document_id_from_doc = profile_doc.get("_id")
                
                # profile_doc['sobriquets_by_group'] 应该只包含 group_key_in_db 的数据 (如果 projection 生效)
                # 或者包含所有群组的数据 (如果 projection 返回了整个字段)
                # SobriquetDB.get_profile_document_by_id_for_find 尝试处理了这一点
                group_data_container = profile_doc.get("sobriquets_by_group", {})
                group_data = group_data_container.get(group_key_in_db) # 从可能包含多个群组的容器中提取特定群组的数据

                raw_sobriquets_list = group_data.get("sobriquets", []) if group_data else []

                if not raw_sobriquets_list:
                    # logger.debug(f"Doc ID {profile_document_id_from_doc}: No sobriquets for group {group_key_in_db}")
                    continue

                formatted_sobriquets = []
                for item in raw_sobriquets_list:
                    if (isinstance(item, dict) and 
                        isinstance(item.get("name"), str) and 
                        isinstance(item.get("count"), int) and item["count"] > 0):
                        formatted_sobriquets.append({item["name"]: item["count"]}) # 保持原格式 {"绰号A": 次数}
                
                if not formatted_sobriquets:
                    # logger.debug(f"Doc ID {profile_document_id_from_doc}: No valid formatted sobriquets.")
                    continue

                original_platform_user_id = profile_doc_id_to_original_uid_map.get(profile_document_id_from_doc)
                if not original_platform_user_id:
                    # logger.warning(f"Doc ID {profile_document_id_from_doc}: Could not map back to original platform user ID.")
                    continue

                person_name = person_names_map.get(original_platform_user_id)
                if not person_name:
                    # logger.warning(f"ProfileManager: 用户 (平台ID: {original_platform_user_id}) 有绰号但未能获取其 person_name。")
                    continue 

                sobriquets_result_map[person_name] = {
                    "user_id": original_platform_user_id,
                    "sobriquets": formatted_sobriquets
                }
        except Exception as e:
            logger.error(f"ProfileManager: 批量获取群组绰号时发生意外错误: {e}", exc_info=True)

        return sobriquets_result_map

# 全局实例，将在 main_test.py 中正确初始化
# profile_manager = ProfileManager() 
# ^^^ 我们将在 main_test.py 中创建实例，以确保 SobriquetDB 被正确初始化和传入
