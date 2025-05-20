# profile/sobriquet/sobriquet_manager.py
import asyncio
import threading
import random
import time
import json
import re
from typing import Dict, Optional, List, Any, Callable, Tuple # 确保 Tuple 被导入

from stubs.mock_config import global_config
from stubs.mock_dependencies import (
    get_logger, 
    person_info_manager as mock_person_info_manager, # 使用模拟的 person_info_manager
    mock_llm_generate_response, 
    MockChatStream, 
    MockMessageRecv, 
    get_raw_msg_before_timestamp_with_chat, 
    build_readable_messages, 
)
# 从 stubs.mock_dependencies 导入模拟的 relationship_manager
from stubs.mock_dependencies import relationship_manager as mock_relationship_manager 


from profile.profile_db import ProfileDB
# ProfileManager 实例会通过构造函数传入，不再直接导入
# from profile.profile_manager import ProfileManager 

# 导入更新后的 sobriquet_mapper 和 sobriquet_utils
from .sobriquet_mapper import build_mapping_prompt
from .sobriquet_utils import select_sobriquets_for_prompt, format_sobriquet_prompt_injection, weighted_sample_without_replacement

logger = get_logger("SobriquetManager")
logger_helper = get_logger("AsyncLoopHelper") 

# --- run_async_loop 函数定义 (与原文件保持一致) ---
def run_async_loop(loop: asyncio.AbstractEventLoop, coro):
    asyncio.set_event_loop(loop)
    try:
        logger_helper.debug(f"Running coroutine in loop {id(loop)}...")
        result = loop.run_until_complete(coro)
        logger_helper.debug(f"Coroutine completed in loop {id(loop)}.")
        return result
    except asyncio.CancelledError:
        logger_helper.info(f"Coroutine in loop {id(loop)} was cancelled.")
    except Exception as e:
        logger_helper.error(f"Error in async loop {id(loop)}: {e}", exc_info=True)
    finally:
        try:
            all_tasks = asyncio.all_tasks(loop)
            current_task = asyncio.current_task(loop) 
            tasks_to_cancel = [task for task in all_tasks if task is not current_task and not task.done()]
            
            if tasks_to_cancel:
                logger_helper.info(f"Cancelling {len(tasks_to_cancel)} outstanding tasks in loop {id(loop)}...")
                for task in tasks_to_cancel:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*tasks_to_cancel, return_exceptions=True))
                logger_helper.info(f"Outstanding tasks cancelled in loop {id(loop)}.")

            if hasattr(loop, 'shutdown_asyncgens'):
                 loop.run_until_complete(loop.shutdown_asyncgens())
                 logger_helper.debug(f"Async generators shutdown in loop {id(loop)}.")

            if loop.is_running():
                loop.stop()
                logger_helper.info(f"Asyncio loop {id(loop)} stopped.")
            if not loop.is_closed():
                loop.close()
                logger_helper.info(f"Asyncio loop {id(loop)} closed.")
        except Exception as close_err:
            logger_helper.error(f"Error during asyncio loop cleanup for loop {id(loop)}: {close_err}", exc_info=True)
# --- run_async_loop 定义结束 ---


class SobriquetManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    logger.info("正在创建 SobriquetManager 单例实例...")
                    cls._instance = super(SobriquetManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_handler: ProfileDB, profile_manager_instance: Any, chat_history_provider: Optional[Dict[str, Any]] = None):
        if hasattr(self, "_initialized") and self._initialized:
            return
        with self._lock:
            if hasattr(self, "_initialized") and self._initialized: 
                return
            
            logger.info("正在初始化 SobriquetManager 组件 (使用 ProfileDB)...")
            self.is_enabled = global_config.profile.enable_sobriquet_mapping

            self.db_handler: ProfileDB = db_handler 
            if not self.db_handler or not self.db_handler.is_available():
                logger.error(f"ProfileDB 初始化失败或不可用，功能受限。")
                self.is_enabled = False # 如果数据库不可用，禁用功能
            else:
                logger.info(f"ProfileDB (SQLite, path: {self.db_handler.db_path}) 初始化成功。")
            
            self.profile_manager = profile_manager_instance # 直接使用传入的 ProfileManager 实例
            if not self.profile_manager or not self.profile_manager.is_available():
                logger.error(f"ProfileManager 不可用，功能将受限。")
                self.is_enabled = False
            
            # LLM 映射器使用模拟函数
            self.llm_mapper_fn = mock_llm_generate_response 
            if self.is_enabled:
                model_config_obj = global_config.model.get("sobriquet_mapping") 
                if model_config_obj and model_config_obj.get("name"): 
                    logger.info(f"绰号映射 LLM (模拟) 配置存在: {model_config_obj.get('name')}.")
                else:
                    logger.warning("绰号映射 LLM 配置无效或缺失 'name'，功能禁用。")
                    self.is_enabled = False
            
            self.chat_history_provider = chat_history_provider if chat_history_provider is not None else {}
            logger.debug(f"SobriquetManager initialized with chat_history_provider (id: {id(self.chat_history_provider)}), keys: {list(self.chat_history_provider.keys())}")

            self.queue_max_size = global_config.profile.sobriquet_queue_max_size
            self.sobriquet_queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_max_size)
            self._stop_event = threading.Event()
            self._sobriquet_thread: Optional[threading.Thread] = None
            self.sleep_interval = global_config.profile.sobriquet_process_sleep_interval
            self._initialized = True
            logger.info(f"SobriquetManager 初始化完成。当前启用状态: {self.is_enabled}")

    def start_processor(self):
        if not self.is_enabled: 
            logger.info("绰号处理功能已禁用，处理器未启动。")
            return
        max_sobriquets_cfg = global_config.profile.max_sobriquets_in_prompt
        if not isinstance(max_sobriquets_cfg, int) or max_sobriquets_cfg <= 0: # 检查配置是否合适
            logger.error(f"[错误] 配置 'max_sobriquets_in_prompt'({max_sobriquets_cfg})不合适，功能禁用！")
            self.is_enabled = False
            return
        if self._sobriquet_thread is None or not self._sobriquet_thread.is_alive():
            logger.info("正在启动绰号处理器线程...")
            self._stop_event.clear()
            self._sobriquet_thread = threading.Thread(target=self._run_processor_in_thread, daemon=True)
            self._sobriquet_thread.start()
            logger.info(f"绰号处理器线程已启动 (ID: {self._sobriquet_thread.ident})")
        else: 
            logger.warning("绰号处理器线程已在运行中。")

    def stop_processor(self):
        if self._sobriquet_thread and self._sobriquet_thread.is_alive():
            logger.info("正在停止绰号处理器线程...")
            self._stop_event.set()
            try: 
                # 尝试向队列放入一个 None 来唤醒等待的 get()
                self.sobriquet_queue.put_nowait(None) # 使用 put_nowait 避免阻塞
            except asyncio.QueueFull: 
                logger.debug("停止处理器时队列已满，项目将在处理后停止。")
            except Exception as e: # 更通用的异常捕获
                logger.warning(f"停止处理器时向队列发送 None 失败: {e}")

            try:
                # 等待线程结束，设置超时
                self._sobriquet_thread.join(timeout=max(2.0, self.sleep_interval * 2 + 1)) # 稍长一点的超时
                if self._sobriquet_thread.is_alive(): 
                    logger.warning("绰号处理器线程在超时后仍未停止。")
            except Exception as e: 
                logger.error(f"停止绰号处理器线程时出错: {e}", exc_info=True)
            finally:
                if self._sobriquet_thread and not self._sobriquet_thread.is_alive(): 
                    logger.info("绰号处理器线程已成功停止。")
                self._sobriquet_thread = None # 清理线程对象
        else: 
            logger.info("绰号处理器线程未在运行或已被清理。")

    async def _add_to_queue(self, item: tuple, platform: str, group_id: str):
        try:
            if self._stop_event.is_set() and item is not None: # 检查停止事件
                 logger.info(f"停止事件已设置，不再添加新项目到队列: {platform}-{group_id}")
                 return
            await self.sobriquet_queue.put(item)
            logger.debug(f"项目已添加至 {platform}-{group_id} 绰号队列。大小: {self.sobriquet_queue.qsize()}")
        except asyncio.QueueFull: 
            logger.warning(f"绰号队列已满 (最大={self.queue_max_size})。{platform}-{group_id} 项目被丢弃。")
        except Exception as e: 
            logger.error(f"添加项目到绰号队列出错: {e}", exc_info=True)

    async def trigger_sobriquet_analysis(
        self, anchor_message: MockMessageRecv, bot_reply: List[str], chat_stream: Optional[MockChatStream] = None
    ):
        if not self.is_enabled: return # 依赖总开关
        
        analysis_probability = global_config.profile.sobriquet_analysis_probability
        if not (0 <= analysis_probability <= 1.0): # 概率值校验
            analysis_probability = 1.0 # 如果配置错误，则默认为1.0
            logger.warning(f"配置 sobriquet_analysis_probability ({global_config.profile.sobriquet_analysis_probability}) 无效，已重置为 1.0")

        if random.random() > analysis_probability: # 概率判断
            logger.debug(f"跳过绰号分析：随机概率未命中 ({analysis_probability:.2f})。")
            return

        current_chat_stream = chat_stream or anchor_message.chat_stream
        if not current_chat_stream or not current_chat_stream.group_info: 
            logger.debug("跳过绰号分析：非群聊或无效的 chat_stream。")
            return
        
        log_prefix = f"[{current_chat_stream.stream_id}]"
        try:
            history_limit = global_config.profile.sobriquet_analysis_history_limit
            # 使用 chat_history_provider 获取历史消息
            history_messages = get_raw_msg_before_timestamp_with_chat(
                current_chat_stream.stream_id, time.time(), history_limit, self.chat_history_provider 
            )
            
            # 构建 user_name_map (与原 SobriquetManager 逻辑一致)
            user_ids_in_history = list(set(str(msg["user_info"]["user_id"]) for msg in history_messages if msg.get("user_info", {}).get("user_id")))
            user_name_map: Dict[str, str] = {} 
            if user_ids_in_history:
                try:
                    # 使用模拟的 relationship_manager
                    names_data = await mock_relationship_manager.get_person_names_batch(current_chat_stream.platform, user_ids_in_history)
                    user_name_map.update(names_data) 
                except Exception as e:
                    logger.error(f"{log_prefix} 批量获取 person_name (模拟) 出错: {e}", exc_info=True)
                
                # 为没有从 relationship_manager 获取到名称的用户提供回退名称
                for uid_str in user_ids_in_history:
                    if uid_str not in user_name_map or not user_name_map[uid_str]:
                        latest_display_name = next((m["user_info"].get("user_cardname") or m["user_info"].get("user_nickname")
                                                   for m in reversed(history_messages) 
                                                   if str(m["user_info"].get("user_id")) == uid_str and 
                                                      (m["user_info"].get("user_nickname") or m["user_info"].get("user_cardname"))), None)
                        bot_uid_str = str(global_config.bot.qq_account)
                        if uid_str == bot_uid_str: # 机器人自己
                            user_name_map[uid_str] = latest_display_name or f"{global_config.bot.nickname}(你)"
                        else: # 其他用户
                            user_name_map[uid_str] = latest_display_name or f"用户({uid_str[-4:] if len(uid_str) >=4 else uid_str})"
            
            chat_history_str = await build_readable_messages(
                history_messages,
                user_name_provider=mock_relationship_manager.get_person_names_batch, # 使用模拟的 provider
                platform_for_names=current_chat_stream.platform
                # 其他参数根据 build_readable_messages 的定义和需要调整
            )

            bot_reply_str = " ".join(bot_reply) if bot_reply else ""
            group_id = str(current_chat_stream.group_info.group_id)
            platform = current_chat_stream.platform
                       
            # item 现在包含 user_name_map，与原 SobriquetManager 逻辑一致
            item = (chat_history_str, bot_reply_str, platform, group_id, user_name_map)
            await self._add_to_queue(item, platform, group_id)
        except Exception as e: 
            logger.error(f"{log_prefix} 触发绰号分析时出错: {e}", exc_info=True)

    async def get_sobriquet_prompt_injection(self, chat_stream: MockChatStream, message_list_before_now: List[Dict]) -> str:
        """
        获取并格式化用于 Prompt 注入的用户绰号信息字符串。
        此方法现在更专注于从 ProfileManager 获取处理好的绰号数据，并进行格式化。
        """
        if not self.is_enabled or not chat_stream or not chat_stream.group_info: return ""
        if not self.profile_manager or not self.profile_manager.is_available(): 
            logger.warning("ProfileManager 不可用，无法获取绰号注入。")
            return ""
        
        log_prefix = f"[{chat_stream.stream_id}]"
        try:
            group_id_str = str(chat_stream.group_info.group_id)
            platform = chat_stream.platform
            
            # 1. 确定上下文中的用户
            user_ids_in_context_set = {str(msg["user_info"]["user_id"]) for msg in message_list_before_now if msg.get("user_info", {}).get("user_id")}
            
            # 如果消息列表为空，尝试从 recent_speakers 获取
            if not user_ids_in_context_set:
                limit = global_config.profile.recent_speakers_limit_for_injection
                recent_speakers_data = chat_stream.get_recent_speakers(limit=limit) # 假设 MockChatStream 有此方法
                user_ids_in_context_set.update(str(s["user_id"]) for s in recent_speakers_data if s.get("user_id"))

            if not user_ids_in_context_set: 
                logger.debug(f"{log_prefix} 无上下文用户用于绰号注入。")
                return ""
            
            user_ids_list = list(user_ids_in_context_set)

            # 2. 从 ProfileManager 获取这些用户在本群的绰号数据 (以及他们的平台昵称/真实名称)
            # ProfileManager.get_users_group_sobriquets_for_prompt_injection_data 期望的输入是 platform_user_ids
            # 返回的是 { "用户名1": {"user_id": "uid1", "sobriquets": [{"绰号A": 次数}, ...]}, ... }
            # 其中键是用户的平台昵称或真实名称
            all_sobriquets_data_from_pm: Dict[str, Dict[str, Any]] = {}
            if self.profile_manager:
                 all_sobriquets_data_from_pm = await self.profile_manager.get_users_group_sobriquets_for_prompt_injection_data(
                    platform, user_ids_list, group_id_str
                )

            if not all_sobriquets_data_from_pm:
                logger.debug(f"{log_prefix} 未从 ProfileManager 获取到用户 {user_ids_list} 在群组 {group_id_str} 的绰号数据。")
                # 即使没有绰号数据，我们仍然可以尝试注入用户的基本信息（如平台昵称）
                # 但此函数的目标是绰号注入，如果 ProfileManager 没有返回绰号，则这里返回空
                return ""

            # 3. 使用 select_sobriquets_for_prompt 选择合适的绰号
            #   输入: { "用户名1": {"user_id": "uid1", "sobriquets": [{"绰号A": 次数}, ...]}, ... }
            #   输出: List[Tuple[str(用户名), str(user_id), str(绰号), int(次数)]]
            selected_sobriquets_tuples: List[Tuple[str, str, str, int]] = select_sobriquets_for_prompt(all_sobriquets_data_from_pm)

            if not selected_sobriquets_tuples:
                logger.debug(f"{log_prefix} 未选择到任何绰号用于注入。")
                return ""

            # 4. 准备 users_data_with_sobriquets 给 format_sobriquet_prompt_injection
            #    我们需要将 selected_sobriquets_tuples 转换成 format_sobriquet_prompt_injection 期望的格式
            #    即 List[Dict[str, Any]]，每个字典包含 "user_id", "platform_nickname", "selected_sobriquets" (List[str])
            
            users_formatted_for_injection: List[Dict[str, Any]] = []
            # 先按 user_id 分组已选绰号
            grouped_selected_sobriquets_by_uid: Dict[str, List[str]] = {}
            platform_nicknames_map: Dict[str, str] = {} # 用于存储 user_id 到 platform_nickname 的映射

            for name, uid, sobriquet_str, _count in selected_sobriquets_tuples:
                if uid not in grouped_selected_sobriquets_by_uid:
                    grouped_selected_sobriquets_by_uid[uid] = []
                grouped_selected_sobriquets_by_uid[uid].append(sobriquet_str)
                if uid not in platform_nicknames_map: # 假设 name 就是 platform_nickname
                    platform_nicknames_map[uid] = name 
            
            # 补充那些在上下文中但可能没有被选中绰号的用户 (如果需要显示他们的基本信息)
            # 但由于此函数专注于绰号注入，我们只处理有选中绰号的用户
            for uid_in_context in user_ids_list:
                user_entry: Dict[str, Any] = {"user_id": uid_in_context}
                
                # 获取平台昵称
                # 尝试从 platform_nicknames_map 获取 (来自有选中绰号的用户)
                # 或者从 all_sobriquets_data_from_pm 的键获取 (如果结构允许)
                # 或者从 message_list_before_now 回退
                plat_nick = platform_nicknames_map.get(uid_in_context)
                if not plat_nick: # 如果用户有绰号数据但没被选中，或者根本没绰号数据
                    # 尝试从原始的 all_sobriquets_data_from_pm 找到对应的 platform_nickname
                    # 这需要 all_sobriquets_data_from_pm 的值包含 user_id，并且其键是 platform_nickname
                    for pn_key, data_val in all_sobriquets_data_from_pm.items():
                        if data_val.get("user_id") == uid_in_context:
                            plat_nick = pn_key
                            break
                if not plat_nick: # 如果还是没有，从 message_list_before_now 回退
                     user_info_latest = next((msg.get("user_info") for msg in reversed(message_list_before_now) 
                                             if msg.get("user_info", {}).get("user_id") == uid_in_context), None)
                     if user_info_latest:
                         plat_nick = user_info_latest.get("user_cardname") or user_info_latest.get("user_nickname") or f"User_{uid_in_context[:4]}"
                     else:
                         plat_nick = f"User_{uid_in_context[:4]}" # 最终回退

                user_entry["platform_nickname"] = plat_nick
                user_entry["selected_sobriquets"] = grouped_selected_sobriquets_by_uid.get(uid_in_context, [])
                
                # 只有当用户有选中的绰号时，才加入到最终的注入列表 (保持原 SobriquetManager 注入逻辑的专注性)
                if user_entry["selected_sobriquets"]:
                    users_formatted_for_injection.append(user_entry)
            
            if not users_formatted_for_injection:
                 logger.debug(f"{log_prefix} 没有用户符合绰号注入的最终条件。")
                 return ""

            # 5. 调用 format_sobriquet_prompt_injection 进行格式化
            injection_str = format_sobriquet_prompt_injection(users_formatted_for_injection, is_group_chat=True)
            
            if injection_str: 
                logger.debug(f"{log_prefix} 生成绰号注入 (部分):\n{injection_str.strip()[:200]}...")
            return injection_str
            
        except Exception as e: 
            logger.error(f"{log_prefix} 获取绰号注入时出错: {e}", exc_info=True)
            return ""

    async def _analyze_and_update_sobriquets(self, item: tuple):
        if not isinstance(item, tuple) or len(item) != 5: # 确保 item 结构正确
            logger.warning(f"从队列接收到无效项目: {type(item)} 内容: {item}")
            return

        chat_history_str, bot_reply, platform, group_id_str, user_name_map = item
        log_prefix = f"[{platform}:{group_id_str}]"

        if not self.llm_mapper_fn: 
            logger.error(f"{log_prefix} LLM 映射函数 (模拟) 不可用。")
            return
        if not self.db_handler or not self.db_handler.is_available(): 
            logger.error(f"{log_prefix} ProfileDB (SQLite) 不可用。")
            return

        # 调用 LLM 进行分析，现在传入 user_name_map
        analysis_result = await self._call_llm_for_analysis(chat_history_str, bot_reply, user_name_map)

        if analysis_result.get("is_exist") and analysis_result.get("data"):
            sobriquet_map_to_update = analysis_result["data"]
            logger.info(f"{log_prefix} LLM (模拟) 找到绰号映射，准备更新: {sobriquet_map_to_update}")

            for platform_user_id_str, sobriquet_name in sobriquet_map_to_update.items():
                if not platform_user_id_str or not sobriquet_name:
                    logger.warning(f"{log_prefix} 跳过无效条目: platform_uid='{platform_user_id_str}', sobriquet='{sobriquet_name}'")
                    continue
                
                try:
                    # 使用模拟的 person_info_manager 获取 person_info_pid
                    person_info_pid = mock_person_info_manager.get_person_id(platform, platform_user_id_str)
                    if not person_info_pid:
                        logger.error(f"{log_prefix} 无法为 platform='{platform}', uid='{platform_user_id_str}' 获取 person_info_pid (模拟)。")
                        continue
                    
                    # 使用 ProfileManager 生成 profile_document_id
                    profile_doc_id = self.profile_manager.generate_profile_document_id(person_info_pid)
                    
                    # 确保文档存在
                    await self.db_handler.ensure_profile_document_exists(
                        profile_doc_id, person_info_pid, platform, platform_user_id_str
                    )
                    
                    # 更新绰号计数
                    await self.db_handler.update_group_sobriquet_count(
                        profile_doc_id, platform, group_id_str, sobriquet_name
                    )
                    
                    logger.debug(f"{log_prefix} 已为 profile_doc_id '{profile_doc_id}' (uid '{platform_user_id_str}') 更新/添加绰号 '{sobriquet_name}' @ grp '{group_id_str}'。")

                except ValueError as ve: 
                     logger.error(f"{log_prefix} 生成 profile_doc_id 失败: {ve} for uid: {platform_user_id_str}, pipid: {person_info_pid if 'person_info_pid' in locals() else 'N/A'}")
                except Exception as e: 
                    logger.exception(f"{log_prefix} 处理用户 {platform_user_id_str} 绰号 '{sobriquet_name}' 时意外错误：{e}")
        else:
            logger.debug(f"{log_prefix} LLM (模拟) 未找到可靠绰号映射或分析失败。")

    async def _call_llm_for_analysis( self, chat_history_str: str, bot_reply: str, user_name_map: Dict[str, str]) -> Dict[str, Any]:
        """
        内部方法：调用 LLM 分析聊天记录和 Bot 回复，提取可靠的 用户ID-绰号 映射。
        现在接收 user_name_map 并传递给 build_mapping_prompt。
        """
        if not self.llm_mapper_fn: 
            logger.error("LLM映射函数 (模拟) 未初始化。"); return {"is_exist": False}
        
        # 构建 prompt 时传入 user_name_map
        prompt = build_mapping_prompt(chat_history_str, bot_reply, user_name_map) 
        logger.debug(f"构建的绰号映射 Prompt (部分):\n{prompt[:300]}...") # 调整日志输出长度

        try:
            # 使用模拟的 LLM 函数
            response_content, _, _ = self.llm_mapper_fn(prompt, context_user_id=None) # 模拟函数可能需要 context_user_id
            
            if not response_content: 
                logger.warning("LLM (模拟) 返回空绰号映射内容。"); return {"is_exist": False}
            
            stripped_content = response_content.strip()
            json_str = ""
            # 增强 JSON 提取逻辑 (与原 SobriquetManager 一致)
            m_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped_content, re.DOTALL)
            if m_match:
                json_str = m_match.group(1).strip()
            elif stripped_content.startswith("{") and stripped_content.endswith("}"):
                json_str = stripped_content
            else: # 尝试更宽松的匹配
                b_match = re.search(r"(\{[\s\S]*?\})", stripped_content) # 匹配被文本包围的 JSON
                if b_match:
                    json_str = b_match.group(1).strip()
                else:
                    logger.warning(f"LLM (模拟) 响应不含有效JSON。响应(首200): {stripped_content[:200]}")
                    return {"is_exist": False}
            
            logger.debug(f"将要解析的 JSON 字符串 (repr): {repr(json_str)}")
            
            try:
                result = json.loads(json_str)
            except json.JSONDecodeError as je: 
                logger.error(f"解析LLM (模拟) JSON失败: {je}\nJSON str: {json_str}\n原始响应(首500): {stripped_content[:500]}"); return {"is_exist": False}
            
            if not isinstance(result, dict): 
                logger.warning(f"LLM (模拟) 响应非字典。类型: {type(result)}"); return {"is_exist": False}
            
            is_exist = result.get("is_exist")
            if not isinstance(is_exist, bool): 
                logger.warning(f"LLM (模拟) 'is_exist'字段无效: {is_exist}"); return {"is_exist": False} 
            
            if is_exist:
                data = result.get("data")
                if isinstance(data, dict) and data:
                    # 调用 _filter_llm_results 进行过滤
                    filtered = self._filter_llm_results(data, user_name_map) 
                    if not filtered: 
                        logger.info("所有绰号映射均被过滤。")
                        return {"is_exist": False, "data": {}}
                    logger.info(f"过滤后绰号映射: {filtered}")
                    return {"is_exist": True, "data": filtered}
                else: # is_exist=True 但 data 无效
                    logger.warning(f"LLM (模拟) is_exist=True 但 data 无效: {data}")
                    return {"is_exist": False, "data": {}} # 返回空的 data
            else: # is_exist=False
                logger.info("LLM (模拟) 明确未找到绰号映射 (is_exist=False)。")
                return {"is_exist": False, "data": {}} # 返回空的 data
        except Exception as e: 
            logger.error(f"LLM (模拟) 调用或处理中意外错误: {e}", exc_info=True)
            return {"is_exist": False, "data": {}}

    def _filter_llm_results(self, original_data: Dict[str, str], user_name_map_for_prompt: Dict[str, str]) -> Dict[str, str]:
        """
        过滤 LLM 返回的绰号映射结果。
        与原 SobriquetManager 的过滤逻辑一致。
        """
        filtered = {}
        bot_qq = str(global_config.bot.qq_account) if global_config.bot.qq_account else None
        min_l, max_l = global_config.profile.sobriquet_min_length, global_config.profile.sobriquet_max_length

        for uid, s_name in original_data.items():
            if not isinstance(uid, str) or not isinstance(s_name, str): 
                logger.warning(f"过滤掉非字符串类型的 uid 或 s_name: uid={uid}, s_name={s_name}")
                continue
            
            user_display_name_in_map = user_name_map_for_prompt.get(uid, "")
            # 过滤机器人自己的绰号
            if bot_qq and uid == bot_qq and \
               ("(你)" in user_display_name_in_map or user_display_name_in_map == global_config.bot.nickname):
                logger.debug(f"过滤机器人自己的绰号映射: uid='{uid}', s_name='{s_name}'")
                continue
            
            # 过滤空或仅含空白的绰号
            if not s_name or s_name.isspace(): 
                logger.debug(f"过滤用户 {uid} 的空绰号。")
                continue
            
            cleaned_s = s_name.strip()
            # 过滤长度不符合要求的绰号
            if not (min_l <= len(cleaned_s) <= max_l): 
                logger.debug(f"过滤绰号'{cleaned_s}' for uid '{uid}':长度({len(cleaned_s)})不符。范围: [{min_l}-{max_l}]")
                continue
            
            filtered[uid] = cleaned_s
        return filtered

    def _run_processor_in_thread(self): 
        tid = threading.get_ident()
        logger.info(f"绰号处理器线程启动 (ID: {tid})...")
        loop = None 
        try:
            loop = asyncio.new_event_loop()
            run_async_loop(loop, self._processing_loop()) 
        except Exception as e: 
            logger.error(f"绰号处理器线程(ID:{tid})顶层错误: {e}", exc_info=True)
        finally: 
            logger.info(f"绰号处理器线程结束 (ID: {tid}).")

    async def _processing_loop(self): 
        logger.info("绰号异步处理循环已启动。")
        while not self._stop_event.is_set():
            try:
                item = await asyncio.wait_for(self.sobriquet_queue.get(), timeout=self.sleep_interval)
                if item is None: # 收到 None 表示需要停止
                    logger.info("处理循环收到 None item, 准备退出。")
                    self.sobriquet_queue.task_done()
                    break 
                
                if self._stop_event.is_set(): # 再次检查停止事件
                    logger.info("处理循环在获取项目后检测到停止事件，退出。")
                    self.sobriquet_queue.task_done()
                    break
                
                await self._analyze_and_update_sobriquets(item)
                self.sobriquet_queue.task_done()
            except asyncio.TimeoutError: 
                continue # 超时是正常的，继续下一次循环
            except asyncio.CancelledError: 
                logger.info("绰号处理循环被取消。")
                break
            except Exception as e:
                logger.error(f"绰号处理循环处理项目时出错: {e}", exc_info=True)
                if not self._stop_event.is_set(): # 如果不是因为停止事件引发的错误
                    await asyncio.sleep(global_config.profile.error_sleep_interval) # 出错后休眠
        
        # 清理队列中剩余的项目 (如果循环是因为 stop_event 而不是 None item 退出)
        while not self.sobriquet_queue.empty():
            try:
                item = self.sobriquet_queue.get_nowait()
                logger.info(f"处理循环结束，丢弃队列中剩余项目: {item is not None}")
                self.sobriquet_queue.task_done()
            except asyncio.QueueEmpty: 
                break # 队列已空
            except Exception as e_drain:
                logger.error(f"清理绰号队列时出错: {e_drain}")
                break # 避免无限循环
        logger.info("绰号异步处理循环已结束。")

