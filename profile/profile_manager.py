# profile/profile_manager.py
import hashlib
import asyncio 
from typing import List, Dict, Any, Optional

from stubs.mock_config import global_config
from stubs.mock_dependencies import get_logger, person_info_manager, relationship_manager 
from profile.profile_db import ProfileDB 

logger = get_logger("ProfileManager")

class ProfileManager:
    def __init__(self, db_path: Optional[str] = None, profile_db_instance: Optional[ProfileDB] = None):
        self.db_path = db_path or global_config.profile.db_path
        
        if profile_db_instance:
            self.db_handler: ProfileDB = profile_db_instance
        else:
            self.db_handler: ProfileDB = ProfileDB(self.db_path) 

        self.profile_id_salt = global_config.security.profile_id_salt
        if self.profile_id_salt == "default_salt_please_change_me" or self.profile_id_salt == "test_salt_for_profile_id":
            logger.warning(f"安全警告：正在使用测试/默认的 profile_id_salt ('{self.profile_id_salt}')。请在生产配置中设置一个强盐值！")
        
        if not self.db_handler.is_available():
            logger.error(
                f"未能初始化 ProfileDB 处理器 (路径: '{self.db_path}')。"
                "ProfileManager 的功能将受限。"
            )
        else:
            logger.info(
                f"ProfileManager 初始化成功，使用 ProfileDB (路径: '{self.db_handler.db_path}')"
            )

    def is_available(self) -> bool:
        return self.db_handler is not None and self.db_handler.is_available()

    def generate_profile_document_id(self, person_info_pid: str) -> str:
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
        (旧方法，用于绰号注入，未来可能被 get_profile_data_for_prompt 替代或整合)
        批量获取多个用户在指定群组的绰号信息。
        """
        if not self.is_available():
            logger.error("ProfileManager: ProfileDB 处理器不可用，无法获取绰号数据。")
            return {}
        
        user_ids_str = [str(uid) for uid in platform_user_ids]
        group_id_str = str(group_id)
        
        profile_doc_ids_to_query: List[str] = []
        profile_doc_id_to_original_uid_map: Dict[str, str] = {}

        for uid_str in user_ids_str:
            person_info_pid = person_info_manager.get_person_id(platform, uid_str)
            if person_info_pid:
                try:
                    profile_doc_id = self.generate_profile_document_id(person_info_pid)
                    if profile_doc_id not in profile_doc_ids_to_query:
                        profile_doc_ids_to_query.append(profile_doc_id)
                    profile_doc_id_to_original_uid_map[profile_doc_id] = uid_str
                except ValueError as e:
                    logger.error(f"为 person_info_pid '{person_info_pid}' (来自 uid '{uid_str}') 生成 profile_doc_id 失败: {e}")
            
        if not profile_doc_ids_to_query:
            logger.debug(f"ProfileManager: 未能为提供的 platform_user_ids 生成任何有效的 profile_document_id。")
            return {}

        try:
            person_names_map: Dict[str, str] = await relationship_manager.get_person_names_batch(platform, user_ids_str)
        except Exception as e:
            logger.error(f"ProfileManager: 调用 (模拟的) get_person_names_batch 时出错: {e}", exc_info=True)
            person_names_map = {}

        sobriquets_result_map: Dict[str, Dict[str, Any]] = {}
        group_key_in_db = f"{platform}-{group_id_str}"

        try:
            projection_for_find = {
                "_id": 1,
                f"sobriquets_by_group.{group_key_in_db}.sobriquets": 1 
            }
            
            for profile_doc_id_to_query in profile_doc_ids_to_query:
                profile_doc = await self.db_handler.get_profile_document_for_find_projection(
                    profile_doc_id_to_query,
                    projection=projection_for_find
                )
                if not profile_doc:
                    continue

                profile_document_id_from_doc = profile_doc.get("_id")
                group_data_container = profile_doc.get("sobriquets_by_group", {})
                group_data = group_data_container.get(group_key_in_db) if group_key_in_db in group_data_container else group_data_container
                raw_sobriquets_list = group_data.get("sobriquets", []) if isinstance(group_data, dict) else []

                if not raw_sobriquets_list: continue
                formatted_sobriquets = []
                for item in raw_sobriquets_list:
                    if (isinstance(item, dict) and 
                        isinstance(item.get("name"), str) and 
                        isinstance(item.get("count"), int) and item["count"] > 0):
                        formatted_sobriquets.append({item["name"]: item["count"]})
                
                if not formatted_sobriquets: continue
                original_platform_user_id = profile_doc_id_to_original_uid_map.get(profile_document_id_from_doc)
                if not original_platform_user_id: continue
                person_name = person_names_map.get(original_platform_user_id)
                if not person_name: continue 

                sobriquets_result_map[person_name] = {
                    "user_id": original_platform_user_id,
                    "sobriquets": formatted_sobriquets
                }
        except Exception as e:
            logger.error(f"ProfileManager: 批量获取群组绰号时发生意外错误: {e}", exc_info=True)
        return sobriquets_result_map

    async def get_profile_data_for_prompt(
        self,
        natural_person_ids_in_context: List[str],
        current_platform: str,
        platform_user_id_to_npid_map: Dict[str, str], 
        current_group_id: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        if not self.is_available():
            logger.error("ProfileManager: ProfileDB 处理器不可用，无法获取画像数据。")
            return {}

        prompt_data: Dict[str, Dict[str, Any]] = {}
        platform_user_ids_for_nickname_lookup: List[str] = []
        npid_to_platform_user_id_map: Dict[str, str] = {}

        for platform_uid, npid_val in platform_user_id_to_npid_map.items():
            if npid_val in natural_person_ids_in_context:
                platform_user_ids_for_nickname_lookup.append(platform_uid)
                npid_to_platform_user_id_map[npid_val] = platform_uid
        
        platform_nicknames_map: Dict[str, str] = {}
        if platform_user_ids_for_nickname_lookup:
            try:
                platform_nicknames_map = await relationship_manager.get_person_names_batch(
                    current_platform, platform_user_ids_for_nickname_lookup
                )
            except Exception as e:
                logger.error(f"获取 platform_nicknames_map 时出错: {e}", exc_info=True)

        for npid in natural_person_ids_in_context:
            profile_doc = await self.db_handler.get_profile_document(npid)
            if not profile_doc:
                logger.warning(f"未能为 NaturalPersonID '{npid}' 获取画像文档。")
                prompt_data[npid] = {} 
                continue

            user_profile_for_prompt: Dict[str, Any] = {}
            specific_platform_user_id = npid_to_platform_user_id_map.get(npid)

            # 1. 构建 current_platform_context
            current_platform_context_data: Dict[str, Any] = {}
            group_nickname_for_current_context = None # 用于 current_platform_context
            
            if specific_platform_user_id:
                platform_nickname = platform_nicknames_map.get(specific_platform_user_id, f"User_{specific_platform_user_id[:4]}")
                
                if current_group_id:
                    sobriquets_by_group = profile_doc.get("sobriquets_by_group", {})
                    group_key = f"{current_platform}-{current_group_id}"
                    group_sobriquets_info = sobriquets_by_group.get(group_key, {}).get("sobriquets", [])
                    if group_sobriquets_info and isinstance(group_sobriquets_info, list):
                        # 按次数排序，取最常用的作为群昵称
                        sorted_group_sobriquets = sorted(group_sobriquets_info, key=lambda x: x.get("count", 0), reverse=True)
                        if sorted_group_sobriquets:
                            group_nickname_for_current_context = sorted_group_sobriquets[0].get("name")
                
                current_platform_context_data[current_platform] = {
                    specific_platform_user_id: {
                        "group_nickname": group_nickname_for_current_context,
                        "platform_nickname": platform_nickname
                    }
                }
            user_profile_for_prompt["current_platform_context"] = current_platform_context_data

            # 2. 构建 all_known_sobriquets_summary (修正逻辑)
            #    根据用户澄清：此字段是当前平台当前聊天流（群）内的常用绰号信息
            current_group_sobriquets_list = []
            if current_group_id: # 只有在群聊上下文中才提取
                sobriquets_by_group_data = profile_doc.get("sobriquets_by_group", {})
                group_key = f"{current_platform}-{current_group_id}"
                current_group_info = sobriquets_by_group_data.get(group_key, {}).get("sobriquets", [])
                if isinstance(current_group_info, list):
                    # 按使用次数排序，取前几个（例如前3个）
                    sorted_sobriquets = sorted(current_group_info, key=lambda x: x.get("count", 0), reverse=True)
                    for sobriquet_item in sorted_sobriquets[:3]: # 最多取3个作为示例
                        if isinstance(sobriquet_item, dict) and "name" in sobriquet_item:
                            current_group_sobriquets_list.append(sobriquet_item["name"])
            
            if current_group_sobriquets_list:
                summary_string = f"在本群常被称为'{ "', '".join(current_group_sobriquets_list) }'"
            else:
                summary_string = "在本群暂无常用绰号记录"
            user_profile_for_prompt["all_known_sobriquets_summary"] = summary_string
            
            # 3. 添加 identity, personality, impression
            user_profile_for_prompt["identity"] = profile_doc.get("identity", {})
            user_profile_for_prompt["personality"] = profile_doc.get("personality", {})
            user_profile_for_prompt["impression"] = profile_doc.get("impression", [])
            
            prompt_data[npid] = user_profile_for_prompt
            
        return prompt_data

