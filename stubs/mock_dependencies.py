# stubs/mock_dependencies.py
# 模拟项目中的其他依赖

import logging
import time
from typing import List, Dict, Any, Optional

# --- 模拟 Logger ---
def get_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

# --- 模拟 src.common.database ---
# 这个全局的 db 对象在原始代码中用于访问 MongoDB 集合。
# 在我们的 SQLite 版本中，SobriquetDB 将直接处理数据库连接。
# ProfileManager 和 SobriquetManager 将被修改为使用 SobriquetDB 实例或其提供的方法。
# 因此，我们可能不需要一个复杂的 db 模拟对象，或者它只是一个概念上的占位符。
# 为了最小化对原始代码的更改，我们可以让 SobriquetDB 成为这个 `db.profile_info` 的实际提供者。
# 但更清晰的做法是直接在 Manager 中实例化或传入 SobriquetDB。
# 这里我们先定义一个简单的占位符，实际的数据库操作将由 SobriquetDB 处理。
class MockDbCollection:
    def __init__(self, db_handler, collection_name):
        self.db_handler = db_handler # SobriquetDB instance
        self.collection_name = collection_name # e.g., "profile_info"
        self.logger = get_logger(f"MockDbCollection({collection_name})")

    # ProfileManager 使用 find 和 update_one (虽然 update_one 不直接用，而是通过 SobriquetDB)
    # 我们需要确保 ProfileManager 的 get_users_group_sobriquets_for_prompt_injection_data
    # 能够通过类似 find 的方式查询数据。
    async def find(self, query: Dict, projection: Optional[Dict] = None) -> List[Dict]:
        # 这个 find 方法需要被 SobriquetDB 实现或适配
        # query example: {"_id": {"$in": profile_doc_ids_to_query}}
        # projection example: {"_id": 1, f"sobriquets_by_group.{group_key_in_db}.sobriquets": 1}
        self.logger.debug(f"Mock find called with query: {query}, projection: {projection}")
        if "_id" in query and "$in" in query["_id"]:
            ids = query["_id"]["$in"]
            results = []
            for doc_id in ids:
                # 调用 SobriquetDB 的方法来获取单个文档
                # SobriquetDB 需要一个 get_document_by_id(doc_id, projection) 方法
                doc = await self.db_handler.get_profile_document_by_id_for_find(doc_id, projection)
                if doc:
                    results.append(doc)
            return results
        self.logger.warning(f"Mock find received unhandled query structure: {query}")
        return []

    # 其他 ProfileManager 可能用到的方法 (如果 ProfileManager 直接操作集合的话)
    # 但我们的目标是让 SobriquetDB 封装这些操作

class MockDatabase:
    def __init__(self, sobriquet_db_instance):
        # getattr(db, "profile_info") 将返回这个 SobriquetDB 实例
        # 或者一个适配器，如果需要更严格地模拟 pymongo.Collection
        # 为了 ProfileManager.py 的 self.profile_collection.find()
        # 我们需要一个带有 find 方法的对象。
        self.profile_info = MockDbCollection(sobriquet_db_instance, "profile_info")

# db 将在 main_test.py 中被实例化并传入 SobriquetDB 实例

# --- 模拟 PersonInfoManager ---
class MockPersonInfoManager:
    def __init__(self):
        # 存储一些模拟数据: (platform, user_id) -> person_info_pid
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

person_info_manager = MockPersonInfoManager() # 全局实例

# --- 模拟 RelationshipManager ---
class MockRelationshipManager:
    def __init__(self):
        # 存储一些模拟数据: (platform, user_id) -> person_name
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

relationship_manager = MockRelationshipManager() # 全局实例


# --- 模拟 LLMRequest 和 LLM 调用 ---
# SobriquetManager 将使用这个函数来获取“手动提供的”LLM响应
MOCK_LLM_RESPONSES = {} # 由测试代码填充

def mock_llm_generate_response(prompt: str, context_user_id: Optional[str] = None) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    模拟 LLM 的响应。
    为了简单起见，我们可以基于 prompt 的某些关键词返回预设的 JSON 字符串。
    在实际测试中，MOCK_LLM_RESPONSES 字典可以被填充。
    """
    logger = get_logger("mock_llm_generate_response")
    logger.debug(f"Mock LLM called with prompt (first 100 chars): {prompt[:100]}...")

    # 尝试从 MOCK_LLM_RESPONSES 字典中查找响应
    # key 可以是与 prompt 内容相关的标识符，或者简单地用 "default"
    # 这里我们用一个简单的逻辑：如果 prompt 包含特定用户ID，则返回特定响应
    
    # 示例：如果 prompt 包含 "user123" 并且我们有为 "user123" 准备的响应
    for key, response_data in MOCK_LLM_RESPONSES.items():
        if key in prompt: # 简单匹配
            logger.info(f"Found mock response for key '{key}' in prompt.")
            # response_data 应该是 LLM 返回的 JSON 字符串
            return response_data, "mock_model_name", "mock_finish_reason"

    # 默认返回一个表示没有找到绰号的 JSON
    default_response = """
    {
        "is_exist": false
    }
    """
    logger.info("No specific mock response found, returning default (is_exist: false).")
    return default_response, "mock_model_name", "mock_finish_reason_default"

def set_mock_llm_response(identifier: str, response_json_string: str):
    """
    用于在测试用例中设置特定的LLM模拟响应。
    identifier: 一个可以在 prompt 中找到的唯一字符串，用于触发此响应。
    response_json_string: LLM应该返回的JSON字符串。
    """
    MOCK_LLM_RESPONSES[identifier] = response_json_string
    get_logger("set_mock_llm_response").info(f"Set mock LLM response for identifier '{identifier}'.")

# --- 模拟 ChatStream 和 MessageRecv (如果 SobriquetManager 的触发逻辑需要) ---
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
        self.messages = [] # 存储消息字典的列表
        self.recent_speakers_list = [] # (user_id, timestamp)

    def add_message(self, user_id, text, timestamp=None):
        msg = {
            "user_info": {"user_id": str(user_id), "user_nickname": f"Nick-{user_id}", "user_cardname": f"Card-{user_id}"},
            "message_content": text,
            "timestamp": timestamp or time.time()
        }
        self.messages.append(msg)
        # 更新最近发言者
        self.recent_speakers_list = [(sp_uid, ts) for sp_uid, ts in self.recent_speakers_list if sp_uid != str(user_id)]
        self.recent_speakers_list.append({"user_id": str(user_id), "timestamp": msg["timestamp"]})
        self.recent_speakers_list.sort(key=lambda x: x["timestamp"], reverse=True)


    def get_recent_speakers(self, limit: int = 5) -> List[Dict[str, Any]]:
        return self.recent_speakers_list[:limit]

class MockMessageRecv:
    def __init__(self, chat_stream: MockChatStream, user_id: str, text: str):
        self.chat_stream = chat_stream
        # 为了能让 get_raw_msg_before_timestamp_with_chat 工作，我们需要一个全局的消息存储或让 chat_stream 存储消息
        # 这里简化，假设 chat_stream 自身可以提供历史消息
        self.user_info = MockUserInfo(user_id)
        self.text = text # 原始消息文本，如果需要的话
        # anchor_message 通常是触发机器人回复的那条消息

# 模拟 src.chat.message_receive.chat_stream.ChatStream 和 message.MessageRecv
# 以及 src.chat.utils.chat_message_builder 中的函数
# 这些主要用于 SobriquetManager.trigger_sobriquet_analysis

# 全局消息存储，按 stream_id 组织
# chat_streams_history: Dict[str, MockChatStream] = {} # 在 main_test.py 中管理

def get_raw_msg_before_timestamp_with_chat(stream_id: str, timestamp: float, limit: int, chat_streams_history: Dict[str, MockChatStream]) -> List[Dict]:
    logger = get_logger("get_raw_msg_before_timestamp_with_chat")
    if stream_id in chat_streams_history:
        stream = chat_streams_history[stream_id]
        # 按时间戳过滤和排序 (假设 stream.messages 是按时间顺序的)
        relevant_messages = [msg for msg in stream.messages if msg["timestamp"] < timestamp]
        relevant_messages.sort(key=lambda x: x["timestamp"], reverse=True) # 最新在前
        logger.debug(f"Found {len(relevant_messages)} messages for stream {stream_id} before {timestamp}. Returning last {limit}.")
        return relevant_messages[:limit]
    logger.warning(f"Stream {stream_id} not found in chat_streams_history.")
    return []

async def build_readable_messages(
    messages: List[Dict],
    include_bot_self: bool = True,
    is_gocq_format: bool = False,
    time_format_type: str = "relative", # "relative", "absolute", "none"
    time_offset_seconds: float = 0.0,
    use_plain_text: bool = False,
) -> str:
    logger = get_logger("build_readable_messages")
    readable_parts = []
    for msg in reversed(messages): # messages 是按时间倒序的 (新->旧), 所以反转一下
        user_id = msg.get("user_info", {}).get("user_id", "UnknownUser")
        name_to_display = msg.get("user_info", {}).get("user_cardname") or \
                          msg.get("user_info", {}).get("user_nickname") or \
                          user_id
        
        content = msg.get("message_content", "")
        # 模拟时间戳格式化
        ts = msg.get("timestamp", 0)
        time_str = ""
        if time_format_type == "absolute":
            time_str = f"({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}) "
        elif time_format_type == "relative":
            relative_time = time.time() - ts
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

