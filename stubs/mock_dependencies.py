# stubs/mock_dependencies.py
# 模拟项目中的其他依赖

import logging
import time
from typing import List, Dict, Any, Optional, Callable, Awaitable, Tuple 

from stubs.mock_config import global_config # 确保 global_config 被导入

# --- 模拟 Logger ---
def get_logger(name):
    logger = logging.getLogger(name)
    # 可以根据需要设置日志级别等
    # logger.setLevel(logging.DEBUG) 
    # handler = logging.StreamHandler()
    # formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # handler.setFormatter(formatter)
    # if not logger.handlers:
    # logger.addHandler(handler)
    return logger

# --- 模拟 src.common.database ---
class MockDbCollection:
    def __init__(self, db_handler, collection_name): # db_handler 现在是 ProfileDB 实例
        self.db_handler = db_handler 
        self.collection_name = collection_name # 在SQLite中，这可能是表名
        self.logger = get_logger(f"MockDbCollection({collection_name})")

    async def find(self, query: Dict, projection: Optional[Dict] = None) -> List[Dict]:
        self.logger.debug(f"Mock find called with query: {query}, projection: {projection}")
        # 模拟基于 ProfileDB 的 find
        if "_id" in query and "$in" in query["_id"]:
            ids_to_find = query["_id"]["$in"]
            results = []
            for doc_id in ids_to_find:
                # ProfileDB 有一个 get_profile_document_for_find_projection 方法
                doc = await self.db_handler.get_profile_document_for_find_projection(doc_id, projection)
                if doc:
                    results.append(doc)
            return results
        self.logger.warning(f"Mock find received unhandled query structure: {query}")
        return []

class MockDatabase:
    def __init__(self, profile_db_instance): # 接收 ProfileDB 实例
        # profile_info 集合现在由 ProfileDB 直接处理，这里可以模拟其接口
        self.profile_info = MockDbCollection(profile_db_instance, global_config.profile.profile_info_collection_name)


# --- 模拟 PersonInfoManager ---
class MockPersonInfoManager:
    def __init__(self):
        self.user_to_pid_map = {
            ("qq", "user123"): "pid_of_user123",
            ("qq", "user456"): "pid_of_user456",
            ("qq", "user789"): "pid_of_user789",
            ("test_platform", "test_user_1"): "pid_test_user_1", # 用于 main_test.py
        }
        self.logger = get_logger("MockPersonInfoManager")

    def get_person_id(self, platform: str, user_id: str) -> Optional[str]:
        pid = self.user_to_pid_map.get((platform, str(user_id)))
        self.logger.debug(f"get_person_id({platform}, {user_id}) -> {pid}")
        return pid

    def add_mock_user(self, platform: str, user_id: str, person_info_pid: str):
        self.user_to_pid_map[(platform, str(user_id))] = person_info_pid
        self.logger.info(f"Added mock user: ({platform}, {user_id}) -> {person_info_pid}")

person_info_manager = MockPersonInfoManager()

# --- 模拟 RelationshipManager ---
class MockRelationshipManager:
    def __init__(self):
        self.user_to_name_map = { # 存储 platform_user_id 到其平台昵称/真实名称的映射
            ("qq", "user123"): "张三",
            ("qq", "user456"): "李四",
            ("qq", "user789"): "王五",
            ("test_platform", "test_user_1"): "张三", # 用于 main_test.py
        }
        # 模拟存储群组绰号数据，结构需要与 NicknameDB/ProfileDB 中的 sobriquets_by_group 类似
        # 但为了简化模拟，这里可以只存储最终结果
        self.mock_group_sobriquets: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        # 例如: {"platform-group_id": {"user_id1": [{"name": "sobriquet1", "count": 5}, ...], ...}}
        self.logger = get_logger("MockRelationshipManager")

    async def get_person_names_batch(self, platform: str, user_ids: List[str]) -> Dict[str, str]:
        """模拟批量获取用户在平台上的昵称或真实名称。"""
        results = {}
        for user_id_str in user_ids: # 确保 user_id 是字符串
            name = self.user_to_name_map.get((platform, str(user_id_str)))
            if name:
                results[str(user_id_str)] = name
        self.logger.debug(f"get_person_names_batch({platform}, {user_ids}) -> {results}")
        return results
    
    def add_mock_user_name(self, platform: str, user_id: str, name: str):
        self.user_to_name_map[(platform, str(user_id))] = name
        self.logger.info(f"Added mock user name: ({platform}, {user_id}) -> {name}")

    async def get_users_group_sobriquets(
        self, platform: str, user_ids: List[str], group_id: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        模拟从 NicknameDB/ProfileDB 获取用户在特定群组的绰号信息。
        返回格式: { "用户实际昵称1": {"user_id": "uid1", "nicknames": [{"绰号A": 次数}, ...]}, ... }
        在 WTF_Profile_system 中，"nicknames" 对应 "sobriquets"。
        """
        results_by_actual_name: Dict[str, Dict[str, Any]] = {}
        group_key = f"{platform}-{group_id}"
        platform_names = await self.get_person_names_batch(platform, user_ids)

        for uid_str in user_ids:
            actual_name = platform_names.get(uid_str)
            if not actual_name:
                actual_name = f"UnknownUser({uid_str})" 

            user_sobriquets_in_group = self.mock_group_sobriquets.get(group_key, {}).get(uid_str, [])
            
            if user_sobriquets_in_group:
                formatted_sobriquets = []
                for s_entry in user_sobriquets_in_group: 
                    if "name" in s_entry and "count" in s_entry:
                         formatted_sobriquets.append({s_entry["name"]: s_entry["count"]})
                
                if formatted_sobriquets:
                    results_by_actual_name[actual_name] = {
                        "user_id": uid_str,
                        "sobriquets": formatted_sobriquets 
                    }
        self.logger.debug(f"Mock get_users_group_sobriquets for {group_key}, users {user_ids} -> {results_by_actual_name}")
        return results_by_actual_name

    def add_mock_group_sobriquet(self, platform: str, group_id: str, user_id: str, sobriquet_name: str, count: int = 1):
        group_key = f"{platform}-{group_id}"
        if group_key not in self.mock_group_sobriquets:
            self.mock_group_sobriquets[group_key] = {}
        if user_id not in self.mock_group_sobriquets[group_key]:
            self.mock_group_sobriquets[group_key][user_id] = []
        
        found = False
        for item in self.mock_group_sobriquets[group_key][user_id]:
            if item["name"] == sobriquet_name:
                item["count"] += count
                found = True
                break
        if not found:
            self.mock_group_sobriquets[group_key][user_id].append({"name": sobriquet_name, "count": count})
        self.logger.info(f"Added/Updated mock group sobriquet for {user_id} in {group_key}: {sobriquet_name} (count updated)")

relationship_manager = MockRelationshipManager()

# --- 模拟 LLMRequest 和 LLM 调用 ---
MOCK_LLM_RESPONSES: Dict[str, str] = {} 

def mock_llm_generate_response(prompt: str, context_user_id: Optional[str] = None) -> tuple[Optional[str], Optional[str], Optional[str]]:
    logger_llm = get_logger("mock_llm_generate_response")
    logger_llm.debug(f"Mock LLM called with prompt (first 100 chars): {prompt[:100]}...")
    for identifier_key in reversed(list(MOCK_LLM_RESPONSES.keys())):
        if identifier_key in prompt: 
            response_json_str = MOCK_LLM_RESPONSES[identifier_key]
            logger_llm.info(f"Found mock response for key '{identifier_key}' in prompt.")
            return response_json_str, "mock_model_name_from_identifier", "mock_finish_reason_identifier_match"
    default_response = """
    {
        "is_exist": false
    }
    """
    logger_llm.info("No specific mock response found (based on identifier in prompt), returning default (is_exist: false).")
    return default_response, "mock_model_name_default", "mock_finish_reason_default"

def set_mock_llm_response(identifier: str, response_json_string: str):
    MOCK_LLM_RESPONSES[identifier] = response_json_string
    get_logger("set_mock_llm_response").info(f"Set mock LLM response for identifier '{identifier}'.")

# --- 模拟 ChatStream 和 MessageRecv ---
class MockUserInfo: 
    def __init__(self, user_id, user_nickname=None, user_cardname=None, user_titlename=None): 
        self.user_id = str(user_id) 
        self.user_nickname = user_nickname if user_nickname else f"Nick-{self.user_id}"
        self.user_cardname = user_cardname if user_cardname else None 
        self.user_titlename = user_titlename if user_titlename else None 

class MockGroupInfo: 
    def __init__(self, group_id):
        self.group_id = str(group_id) 

class MockChatStream:
    def __init__(self, stream_id, platform, group_id):
        self.stream_id = str(stream_id)
        self.platform = str(platform)
        self.group_info = MockGroupInfo(group_id) if group_id else None 
        self.messages: List[Dict[str, Any]] = [] 
        self.recent_speakers_list: List[Dict[str, Any]] = [] 
        self.logger = get_logger(f"MockChatStream({self.stream_id})")

    def add_message(self, user_id: str, text: str, timestamp: Optional[float] = None, 
                    user_nickname: Optional[str] = None, 
                    user_cardname: Optional[str] = None,
                    user_titlename: Optional[str] = None): 
        msg_timestamp = timestamp if timestamp is not None else time.time()
        current_user_id_str = str(user_id) 
        name_from_rel_mgr = relationship_manager.user_to_name_map.get((self.platform, current_user_id_str))
        user_info_data = {
            "platform": self.platform, 
            "user_id": current_user_id_str,
            "user_nickname": user_nickname or name_from_rel_mgr or f"Nick-{current_user_id_str}",
            "user_cardname": user_cardname, 
            "user_titlename": user_titlename 
        }
        msg = {
            "chat_id": self.stream_id, 
            "user_info": user_info_data,
            "message_content": text, 
            "processed_plain_text": text, 
            "time": msg_timestamp, 
        }
        self.messages.append(msg)
        self.recent_speakers_list = [
            speaker_info for speaker_info in self.recent_speakers_list 
            if speaker_info.get("user_id") != current_user_id_str 
        ]
        self.recent_speakers_list.insert(0, {"user_id": current_user_id_str, "timestamp": msg_timestamp}) 
        limit_recent_speakers = global_config.profile.recent_speakers_limit_for_injection 
        if len(self.recent_speakers_list) > limit_recent_speakers:
            self.recent_speakers_list = self.recent_speakers_list[:limit_recent_speakers]
        self.logger.debug(f"Message added by user_id {current_user_id_str}. Total messages: {len(self.messages)}")

    def get_recent_speakers(self, limit: int = 5) -> List[Dict[str, Any]]:
        return self.recent_speakers_list[:limit]

class MockMessageRecv: 
    def __init__(self, chat_stream: MockChatStream, user_id: str, text: str, 
                 user_nickname: Optional[str] = None, 
                 user_cardname: Optional[str] = None,
                 user_titlename: Optional[str] = None): 
        self.chat_stream = chat_stream
        self.user_info = MockUserInfo(user_id, user_nickname, user_cardname, user_titlename)
        self.text = text 
        self.processed_plain_text = text 
        self.time = time.time() 

# --- 模拟聊天记录获取 ---
def get_raw_msg_before_timestamp_with_chat(
    chat_id: str, timestamp: float, limit: int, chat_streams_history: Dict[str, MockChatStream]
) -> List[Dict]:
    logger_grh = get_logger("get_raw_msg_before_timestamp_with_chat")
    if chat_id in chat_streams_history:
        stream = chat_streams_history[chat_id]
        relevant_messages = [msg for msg in stream.messages if msg["time"] < timestamp]
        relevant_messages.sort(key=lambda x: x["time"]) 
        if limit > 0 and len(relevant_messages) > limit:
            relevant_messages = relevant_messages[-limit:] 
        logger_grh.debug(f"Found {len(relevant_messages)} messages for stream {chat_id} before {timestamp}. Returning up to {limit} (latest among them).")
        return relevant_messages
    logger_grh.warning(f"Stream {chat_id} not found in chat_streams_history for history lookup.")
    return []

# --- 模拟时间戳转换 ---
def translate_timestamp_to_human_readable_mock(
    timestamp: float, mode: str = "relative", current_time_override: Optional[float] = None
) -> str:
    current_time = current_time_override if current_time_override is not None else time.time()
    if mode == "absolute":
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
    elif mode == "relative":
        delta = current_time - timestamp
        if delta < 0: delta = 0 
        if delta < 5: return "刚刚"
        if delta < 60: return f"{int(delta)}秒前"
        if delta < 3600: return f"{int(delta/60)}分钟前"
        if delta < 86400: return f"{int(delta/3600)}小时前"
        if delta < 2592000: return f"{int(delta/86400)}天前" 
        return time.strftime('%Y-%m-%d', time.localtime(timestamp)) 
    return str(timestamp)

# --- 更新的 build_readable_messages 模拟函数 ---
async def build_readable_messages(
    messages: List[Dict[str, Any]],
    user_name_provider: Optional[Callable[[str, List[str]], Awaitable[Dict[str, str]]]] = None, 
    platform_for_names: Optional[str] = None, 
    replace_bot_name: bool = True, 
    merge_messages: bool = False, 
    timestamp_mode: str = "relative", 
    read_mark: float = 0.0, 
    truncate: bool = False, 
) -> str:
    logger_bm = get_logger("build_readable_messages_mock")
    if not messages:
        return ""

    async def _build_internal_mock(
        msgs_to_process: List[Dict[str, Any]],
        current_time_for_relative_ts: float
    ) -> Tuple[str, List[Tuple[float, str, str]]]:
        if not msgs_to_process:
            return "", []

        message_details_raw: List[Tuple[float, str, str]] = [] 
        
        user_ids_for_name_lookup = list(set(
            str(msg.get("user_info", {}).get("user_id")) for msg in msgs_to_process if msg.get("user_info", {}).get("user_id")
        ))
        
        platform_nicknames_map: Dict[str, str] = {}
        if user_name_provider and platform_for_names and user_ids_for_name_lookup:
            try:
                platform_nicknames_map = await user_name_provider(platform_for_names, user_ids_for_name_lookup)
            except Exception as e_name_provider:
                logger_bm.error(f"Error calling user_name_provider: {e_name_provider}")
        
        for msg in msgs_to_process:
            user_info = msg.get("user_info", {})
            msg_platform = user_info.get("platform", platform_for_names) 
            user_id_str = str(user_info.get("user_id"))
            msg_timestamp = msg.get("time") 
            msg_content = msg.get("processed_plain_text", msg.get("message_content", ""))

            if not msg_platform or not user_id_str or msg_timestamp is None:
                continue

            sender_display_name = platform_nicknames_map.get(user_id_str) 
            if not sender_display_name: 
                sender_display_name = user_info.get("user_cardname") or \
                                      user_info.get("user_nickname")
            
            if replace_bot_name and user_id_str == str(global_config.bot.qq_account):
                sender_display_name = f"{global_config.bot.nickname}(你)" 
            elif not sender_display_name: 
                 sender_display_name = f"User_{user_id_str[-4:] if len(user_id_str) >=4 else user_id_str}"

            if msg_content: 
                message_details_raw.append((msg_timestamp, sender_display_name, msg_content))
        
        if not message_details_raw:
            return "", []
        
        message_details_raw.sort(key=lambda x: x[0]) 

        processed_message_details: List[Tuple[float, str, str]] = []
        n_messages = len(message_details_raw)
        if truncate and n_messages > 0 and global_config.profile.get('long_message_auto_truncate', False): 
            for i, (ts, name, content) in enumerate(message_details_raw):
                percentile = i / n_messages
                original_len = len(content)
                limit = -1
                replace_suffix = ""
                if percentile < 0.5: limit = 50; replace_suffix = "...(旧消息内容较长)"
                elif percentile < 0.8: limit = 150; replace_suffix = "...(内容较长)"
                else: limit = 300; replace_suffix = "...(内容略长)"

                truncated_content = content
                if 0 < limit < original_len:
                    truncated_content = f"{content[:limit]}{replace_suffix}"
                processed_message_details.append((ts, name, truncated_content))
        else:
            processed_message_details = message_details_raw

        final_message_blocks = []
        if merge_messages and processed_message_details:
            current_block = {
                "name": processed_message_details[0][1],
                "start_time": processed_message_details[0][0],
                "end_time": processed_message_details[0][0],
                "contents": [processed_message_details[0][2]],
            }
            for i in range(1, len(processed_message_details)):
                ts, name, content = processed_message_details[i]
                if name == current_block["name"] and (ts - current_block["end_time"] <= 60): 
                    current_block["contents"].append(content)
                    current_block["end_time"] = ts
                else:
                    final_message_blocks.append(current_block)
                    current_block = {"name": name, "start_time": ts, "end_time": ts, "contents": [content]}
            final_message_blocks.append(current_block)
        elif processed_message_details: 
            for ts, name, content in processed_message_details:
                final_message_blocks.append({"name": name, "start_time": ts, "end_time": ts, "contents": [content]})
        
        # 修改格式化逻辑以严格对齐 chat_message_builder.py
        output_lines = []
        for block in final_message_blocks:
            time_str = translate_timestamp_to_human_readable_mock(block["start_time"], timestamp_mode, current_time_for_relative_ts)
            
            # 构建与 chat_message_builder.py 一致的 header
            header = f"{time_str}：\n{block['name']} 说:" # 注意这里的换行符
            output_lines.append(header)
            
            for content_line in block["contents"]:
                stripped_line = content_line.strip()
                if stripped_line:
                    # chat_message_builder.py 的内容行是 "  {stripped_line}"
                    output_lines.append(f"  {stripped_line}")
            
            # chat_message_builder.py 在每个块后附加 "\n"
            output_lines.append("\n") 
            
        # 使用 "".join() 并 strip() 最终结果，与 chat_message_builder.py 一致
        return "".join(output_lines).strip(), processed_message_details

    current_time = time.time() 

    if read_mark <= 0:
        formatted_string, _ = await _build_internal_mock(messages, current_time)
        return formatted_string
    else:
        messages_before_mark = [msg for msg in messages if msg.get("time", 0) <= read_mark]
        messages_after_mark = [msg for msg in messages if msg.get("time", 0) > read_mark]

        formatted_before, _ = await _build_internal_mock(messages_before_mark, current_time)
        formatted_after, _ = await _build_internal_mock(messages_after_mark, current_time)
        
        readable_read_mark_time = translate_timestamp_to_human_readable_mock(read_mark, timestamp_mode, current_time)
        # 确保 read_mark_line 的格式与 chat_message_builder.py 一致
        read_mark_line = f"\n--- 以上消息是你已经思考过的内容已读 (标记时间: {readable_read_mark_time}) ---\n--- 请关注以下未读的新消息---\n"
        # 根据 chat_message_builder.py 的组合逻辑调整
        if formatted_before and formatted_after:
            return f"{formatted_before}{read_mark_line}{formatted_after}" # 假设它们已经 strip() 过了
        elif formatted_before:
            return f"{formatted_before}{read_mark_line.strip()}" # 如果只有已读，标记行可能需要 strip
        elif formatted_after:
             # 如果只有未读，确保标记行前有换行（如果它不是以换行开始）
            return f"{read_mark_line.strip()}\n{formatted_after}" if not read_mark_line.startswith("\n") else f"{read_mark_line}{formatted_after}"
        else: 
            return read_mark_line.strip() # 只有标记行
