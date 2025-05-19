# stubs/mock_dependencies.py
# 模拟项目中的其他依赖

import logging
import time
from typing import List, Dict, Any, Optional

# --- 模拟 Logger ---
def get_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG) # 默认级别可以设为 DEBUG，方便调试
    if not logger.handlers: # 避免重复添加 handler
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
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
                doc = await self.db_handler.get_profile_document_by_id_for_find(doc_id, projection)
                if doc:
                    results.append(doc)
            return results
        self.logger.warning(f"Mock find received unhandled query structure: {query}")
        return []

class MockDatabase:
    def __init__(self, sobriquet_db_instance):
        self.profile_info = MockDbCollection(sobriquet_db_instance, "profile_info")

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
            ("test_platform", "test_user_1"): "测试用户一",
        }
        self.logger = get_logger("MockRelationshipManager")

    async def get_person_names_batch(self, platform: str, user_ids: List[str]) -> Dict[str, str]:
        results = {}
        for user_id in user_ids:
            name = self.user_to_name_map.get((platform, str(user_id)))
            if name:
                results[str(user_id)] = name
        self.logger.debug(f"get_person_names_batch({platform}, {user_ids}) -> {results}")
        return results
    
    def add_mock_user_name(self, platform: str, user_id: str, name: str):
        self.user_to_name_map[(platform, str(user_id))] = name
        self.logger.info(f"Added mock user name: ({platform}, {user_id}) -> {name}")

relationship_manager = MockRelationshipManager()

# --- 模拟 LLMRequest 和 LLM 调用 ---
MOCK_LLM_RESPONSES = {} 

def mock_llm_generate_response(prompt: str, context_user_id: Optional[str] = None) -> tuple[Optional[str], Optional[str], Optional[str]]:
    logger = get_logger("mock_llm_generate_response")
    logger.debug(f"Mock LLM called with prompt (first 100 chars): {prompt[:100]}...")
    for key, response_data in MOCK_LLM_RESPONSES.items():
        if key in prompt: 
            logger.info(f"Found mock response for key '{key}' in prompt.")
            return response_data, "mock_model_name", "mock_finish_reason"
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
        self.user_id = user_id
        self.user_nickname = user_nickname if user_nickname else f"Nick-{user_id}"
        self.user_cardname = user_cardname if user_cardname else f"Card-{user_id}"

class MockGroupInfo:
    def __init__(self, group_id):
        self.group_id = group_id

class MockChatStream:
    def __init__(self, stream_id, platform, group_id):
        self.stream_id = stream_id
        self.platform = platform
        self.group_info = MockGroupInfo(group_id)
        self.messages: List[Dict[str, Any]] = [] 
        self.recent_speakers_list: List[Dict[str, Any]] = [] # 确保是字典列表

    def add_message(self, user_id: str, text: str, timestamp: Optional[float] = None):
        # 准备消息字典
        msg_timestamp = timestamp or time.time()
        msg = {
            "user_info": {"user_id": str(user_id), "user_nickname": f"Nick-{user_id}", "user_cardname": f"Card-{user_id}"},
            "message_content": text,
            "timestamp": msg_timestamp
        }
        self.messages.append(msg)

        # --- 更新最近发言者列表 (recent_speakers_list) ---
        # 1. 从列表中移除该用户的旧条目（如果存在）
        #    我们期望 recent_speakers_list 中的每个元素都是一个字典，例如：{'user_id': 'some_id', 'timestamp': 123.45}
        self.recent_speakers_list = [
            speaker_info for speaker_info in self.recent_speakers_list 
            if speaker_info.get("user_id") != str(user_id)
        ]
        
        # 2. 添加/更新该用户为最新发言者（作为字典）
        self.recent_speakers_list.append({"user_id": str(user_id), "timestamp": msg_timestamp})
        
        # 3. 按时间戳降序排序 (最新的在前)
        #    现在列表中的所有元素都应该是字典，所以 x["timestamp"] 可以正常工作
        self.recent_speakers_list.sort(key=lambda x: x["timestamp"], reverse=True)
        # --- 更新结束 ---

    def get_recent_speakers(self, limit: int = 5) -> List[Dict[str, Any]]:
        # 返回最近发言者的列表副本（字典列表）
        return self.recent_speakers_list[:limit]

class MockMessageRecv:
    def __init__(self, chat_stream: MockChatStream, user_id: str, text: str):
        self.chat_stream = chat_stream
        self.user_info = MockUserInfo(user_id) # user_info 应该是 MockUserInfo 实例
        self.text = text 

# 全局消息存储，按 stream_id 组织
# chat_streams_history: Dict[str, MockChatStream] = {} # 在 main_test.py 中管理

def get_raw_msg_before_timestamp_with_chat(stream_id: str, timestamp: float, limit: int, chat_streams_history: Dict[str, MockChatStream]) -> List[Dict]:
    logger = get_logger("get_raw_msg_before_timestamp_with_chat")
    if stream_id in chat_streams_history:
        stream = chat_streams_history[stream_id]
        relevant_messages = [msg for msg in stream.messages if msg["timestamp"] < timestamp]
        relevant_messages.sort(key=lambda x: x["timestamp"], reverse=True) 
        logger.debug(f"Found {len(relevant_messages)} messages for stream {stream_id} before {timestamp}. Returning last {limit}.")
        return relevant_messages[:limit] # 返回最新的 limit 条
    logger.warning(f"Stream {stream_id} not found in chat_streams_history.")
    return []

async def build_readable_messages(
    messages: List[Dict],
    include_bot_self: bool = True,
    is_gocq_format: bool = False,
    time_format_type: str = "relative", 
    time_offset_seconds: float = 0.0,
    use_plain_text: bool = False,
) -> str:
    logger = get_logger("build_readable_messages")
    readable_parts = []
    # messages 传入时已经是按时间倒序的 (新->旧), get_raw_msg_before_timestamp_with_chat 返回的就是这样的
    # 所以这里直接遍历，或者在 get_raw_msg_before_timestamp_with_chat 返回前反转一次以符合“历史记录”的直觉
    # 当前 build_readable_messages 假设 messages 是按时间顺序 (旧->新) 的，所以它内部会 reversed(messages)
    # 我们让 get_raw_msg_before_timestamp_with_chat 返回的是 新->旧，所以这里不需要再反转
    
    # 为了与原始 build_readable_messages 行为一致 (它期望消息是时间顺序，然后它反转)
    # 我们这里先反转一下 get_raw_msg_before_timestamp_with_chat 返回的列表
    
    for msg in reversed(messages): # 假设 messages 是 新->旧，反转后是 旧->新
        user_id = msg.get("user_info", {}).get("user_id", "UnknownUser")
        name_to_display = msg.get("user_info", {}).get("user_cardname") or \
                          msg.get("user_info", {}).get("user_nickname") or \
                          user_id
        
        content = msg.get("message_content", "")
        ts = msg.get("timestamp", 0)
        time_str = ""
        current_time = time.time() + time_offset_seconds # 考虑偏移
        if time_format_type == "absolute":
            time_str = f"({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}) "
        elif time_format_type == "relative":
            relative_time = current_time - ts
            if relative_time < 0: relative_time = 0 # 避免负数 (例如时间戳来自未来)
            
            if relative_time < 60:
                time_str = f"({int(relative_time)}秒前) "
            elif relative_time < 3600:
                time_str = f"({int(relative_time/60)}分钟前) "
            else:
                time_str = f"({int(relative_time/3600)}小时前) "
        
        readable_parts.append(f"{time_str}{name_to_display}: {content}")
    
    result = "\n".join(readable_parts)
    logger.debug(f"Built readable messages (first 100 chars): {result[:100]}")
    return result
