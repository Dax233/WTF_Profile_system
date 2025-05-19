# stubs/mock_dependencies.py
# 模拟项目中的其他依赖

import logging
import time
from typing import List, Dict, Any, Optional, Callable, Awaitable 

from stubs.mock_config import global_config
# --- 模拟 Logger ---
def get_logger(name):
    logger = logging.getLogger(name)
    return logger

# --- 模拟 src.common.database ---
class MockDbCollection:
    def __init__(self, db_handler, collection_name):
        self.db_handler = db_handler 
        self.collection_name = collection_name 
        self.logger = get_logger(f"MockDbCollection({collection_name})")

    async def find(self, query: Dict, projection: Optional[Dict] = None) -> List[Dict]:
        self.logger.debug(f"Mock find called with query: {query}, projection: {projection}")
        if "_id" in query and "$in" in query["_id"]:
            ids = query["_id"]["$in"]
            results = []
            for doc_id in ids:
                doc = await self.db_handler.get_profile_document_for_find_projection(doc_id, projection)
                if doc:
                    results.append(doc)
            return results
        self.logger.warning(f"Mock find received unhandled query structure: {query}")
        return []

class MockDatabase:
    def __init__(self, profile_db_instance): 
        self.profile_info = MockDbCollection(profile_db_instance, "profile_info")

# --- 模拟 PersonInfoManager ---
class MockPersonInfoManager:
    def __init__(self):
        self.user_to_pid_map = {
            ("qq", "user123"): "pid_of_user123",
            ("qq", "user456"): "pid_of_user456",
            ("qq", "user789"): "pid_of_user789",
            ("test_platform", "test_user_1"): "pid_test_user_1",
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
        self.user_to_name_map = {
            ("qq", "user123"): "张三",
            ("qq", "user456"): "李四",
            ("qq", "user789"): "王五",
            ("test_platform", "test_user_1"): "张三", 
        }
        self.logger = get_logger("MockRelationshipManager")

    async def get_person_names_batch(self, platform: str, user_ids: List[str]) -> Dict[str, str]:
        results = {}
        for user_id_str in user_ids: 
            name = self.user_to_name_map.get((platform, str(user_id_str)))
            if name:
                results[str(user_id_str)] = name
        self.logger.debug(f"get_person_names_batch({platform}, {user_ids}) -> {results}")
        return results
    
    def add_mock_user_name(self, platform: str, user_id: str, name: str):
        self.user_to_name_map[(platform, str(user_id))] = name
        self.logger.info(f"Added mock user name: ({platform}, {user_id}) -> {name}")

relationship_manager = MockRelationshipManager()

# --- 模拟 LLMRequest 和 LLM 调用 ---
MOCK_LLM_RESPONSES: Dict[str, str] = {} 

def mock_llm_generate_response(prompt: str, context_user_id: Optional[str] = None) -> tuple[Optional[str], Optional[str], Optional[str]]:
    logger = get_logger("mock_llm_generate_response")
    logger.debug(f"Mock LLM called with prompt (first 100 chars): {prompt[:100]}...")
    for key in reversed(list(MOCK_LLM_RESPONSES.keys())):
        response_data = MOCK_LLM_RESPONSES[key]
        if key in prompt: 
            logger.info(f"Found mock response for key '{key}' (searched in reverse) in prompt.")
            return response_data, "mock_model_name", "mock_finish_reason_reversed_match"
    default_response = """
    {
        "is_exist": false
    }
    """
    logger.info("No specific mock response found, returning default (is_exist: false).")
    return default_response, "mock_model_name", "mock_finish_reason_default"

def set_mock_llm_response(identifier: str, response_json_string: str):
    MOCK_LLM_RESPONSES[identifier] = response_json_string
    get_logger("set_mock_llm_response").info(f"Set mock LLM response for identifier '{identifier}'.")


# --- 模拟 ChatStream 和 MessageRecv ---
class MockUserInfo:
    def __init__(self, user_id, user_nickname=None, user_cardname=None):
        self.user_id = str(user_id) 
        self.user_nickname = user_nickname if user_nickname else f"Nick-{self.user_id}"
        self.user_cardname = user_cardname if user_cardname else f"Card-{self.user_id}"

class MockGroupInfo:
    def __init__(self, group_id):
        self.group_id = str(group_id) 

class MockChatStream:
    def __init__(self, stream_id, platform, group_id):
        self.stream_id = str(stream_id)
        self.platform = str(platform)
        self.group_info = MockGroupInfo(group_id)
        self.messages: List[Dict[str, Any]] = [] 
        self.recent_speakers_list: List[Dict[str, Any]] = []
        self.logger = get_logger(f"MockChatStream({self.stream_id})") # <--- 初始化 logger

    def add_message(self, user_id: str, text: str, timestamp: Optional[float] = None):
        msg_timestamp = timestamp or time.time()
        current_user_id_str = str(user_id) 
        
        user_display_name_info = relationship_manager.user_to_name_map.get((self.platform, current_user_id_str))
        
        msg = {
            "user_info": {
                "user_id": current_user_id_str, 
                "user_nickname": user_display_name_info or f"Nick-{current_user_id_str}", 
                "user_cardname": user_display_name_info or f"Card-{current_user_id_str}"
            },
            "message_content": text,
            "timestamp": msg_timestamp
        }
        self.messages.append(msg)

        self.recent_speakers_list = [
            speaker_info for speaker_info in self.recent_speakers_list 
            if speaker_info.get("user_id") != current_user_id_str
        ]
        self.recent_speakers_list.append({"user_id": current_user_id_str, "timestamp": msg_timestamp})
        self.recent_speakers_list.sort(key=lambda x: x["timestamp"], reverse=True)
        self.logger.debug(f"Message added by user_id {current_user_id_str}. Total messages: {len(self.messages)}") # <--- 使用 self.logger


    def get_recent_speakers(self, limit: int = 5) -> List[Dict[str, Any]]:
        return self.recent_speakers_list[:limit]

class MockMessageRecv:
    def __init__(self, chat_stream: MockChatStream, user_id: str, text: str):
        self.chat_stream = chat_stream
        self.user_info = MockUserInfo(user_id) 
        self.text = text 

def get_raw_msg_before_timestamp_with_chat(stream_id: str, timestamp: float, limit: int, chat_streams_history: Dict[str, MockChatStream]) -> List[Dict]:
    logger = get_logger("get_raw_msg_before_timestamp_with_chat")
    if stream_id in chat_streams_history:
        stream = chat_streams_history[stream_id]
        relevant_messages = [msg for msg in stream.messages if msg["timestamp"] < timestamp]
        relevant_messages.sort(key=lambda x: x["timestamp"], reverse=True) 
        logger.debug(f"Found {len(relevant_messages)} messages for stream {stream_id} before {timestamp}. Returning last {limit}.")
        return relevant_messages[:limit] 
    logger.warning(f"Stream {stream_id} not found in chat_streams_history for history lookup.")
    return []

async def build_readable_messages(
    messages: List[Dict], 
    user_name_provider: Optional[Callable[[str, List[str]], Awaitable[Dict[str, str]]]] = None, 
    platform_for_names: Optional[str] = None, 
    include_bot_self: bool = True, 
    is_gocq_format: bool = False, 
    time_format_type: str = "relative", 
    time_offset_seconds: float = 0.0,
    use_plain_text: bool = False, 
) -> str:
    logger_bm = get_logger("build_readable_messages")
    readable_parts = []
    
    user_ids_in_messages = list(set(msg.get("user_info", {}).get("user_id") for msg in messages if msg.get("user_info", {}).get("user_id")))
    
    display_names_map: Dict[str, str] = {}
    if user_name_provider and platform_for_names and user_ids_in_messages:
        try:
            display_names_map = await user_name_provider(platform_for_names, user_ids_in_messages)
        except Exception as e:
            logger_bm.error(f"Error calling user_name_provider: {e}")

    for msg in reversed(messages): 
        user_info = msg.get("user_info", {})
        user_id = user_info.get("user_id", "UnknownUser")
        
        name_to_display = display_names_map.get(user_id)
        
        if not name_to_display: 
            name_to_display = user_info.get("user_cardname") or \
                              user_info.get("user_nickname")
        
        if not name_to_display: 
            if user_id == str(global_config.bot.qq_account): 
                 name_to_display = f"{global_config.bot.nickname}(你)"
            else:
                 name_to_display = f"User({user_id[-4:] if len(user_id) >=4 else user_id})"

        content = msg.get("message_content", "")
        ts = msg.get("timestamp", 0)
        time_str = ""
        current_time = time.time() + time_offset_seconds
        if time_format_type == "absolute":
            time_str = f"({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}) "
        elif time_format_type == "relative":
            relative_time = current_time - ts
            if relative_time < 0: relative_time = 0 
            
            if relative_time < 60:
                time_str = f"({int(relative_time)}秒前) "
            elif relative_time < 3600:
                time_str = f"({int(relative_time/60)}分钟前) "
            else:
                time_str = f"({int(relative_time/3600)}小时前) "
        
        readable_parts.append(f"{time_str}{name_to_display}: {content}")
    
    result = "\n".join(readable_parts)
    logger_bm.debug(f"Built readable messages (first 100 chars): {result[:100]}")
    return result
