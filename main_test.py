# main_test.py
import asyncio
import time
import os
import json # 用于设置模拟LLM响应

# 导入修改后的管理器和数据库处理器
from profile.sobriquet.sobriquet_db import SobriquetDB
from profile_manager import ProfileManager
from profile.sobriquet.sobriquet_manager import SobriquetManager

# 导入模拟依赖
from stubs.mock_config import global_config
from stubs.mock_dependencies import (
    get_logger,
    person_info_manager as mock_person_info_manager, # 使用模拟实例
    relationship_manager as mock_relationship_manager, # 使用模拟实例
    set_mock_llm_response, # 用于设置LLM响应
    MockChatStream,
    MockMessageRecv,
    # get_raw_msg_before_timestamp_with_chat, # 这些由 SobriquetManager 内部使用
    # build_readable_messages
)

logger = get_logger("MainTest")

# --- 全局聊天记录存储 (用于模拟环境) ---
# SobriquetManager.trigger_sobriquet_analysis 内部会调用 get_raw_msg_before_timestamp_with_chat
# 这个函数需要一个 chat_streams_history 参数。
# 我们将在 SobriquetManager 初始化时传入这个字典。
chat_streams_history: Dict[str, MockChatStream] = {}


async def run_test_scenario():
    logger.info("开始测试场景...")

    # --- 0. 清理旧的数据库文件 (如果存在) ---
    db_file = global_config.profile.db_path
    if os.path.exists(db_file):
        logger.info(f"删除旧的数据库文件: {db_file}")
        os.remove(db_file)

    # --- 1. 初始化数据库和管理器 ---
    # SobriquetDB (SQLite)
    sobriquet_db = SobriquetDB(db_path=db_file)
    logger.info(f"SobriquetDB (SQLite) 初始化完成，数据库文件: {sobriquet_db.db_path}")

    # ProfileManager (使用 SQLite)
    # 它内部会创建自己的 SobriquetDB 实例或使用传入的
    profile_manager = ProfileManager(sobriquet_db_instance=sobriquet_db)
    logger.info("ProfileManager (SQLite) 初始化完成。")

    # SobriquetManager (使用 SQLite, 模拟LLM)
    # 将 chat_streams_history 传入
    sobriquet_manager = SobriquetManager(
        db_handler=sobriquet_db, 
        profile_manager_instance=profile_manager,
        chat_history_provider=chat_streams_history # 传入历史记录
    )
    logger.info("SobriquetManager (SQLite, MockLLM) 初始化完成。")

    # 启动 SobriquetManager 的后台处理线程
    sobriquet_manager.start_processor()
    logger.info("SobriquetManager 处理器已启动。")

    # --- 2. 准备模拟数据 ---
    platform = "test_platform"
    group_id1 = "group101"
    user_id1 = "test_user_1" # 张三 (在 mock_dependencies 中定义) -> pid_test_user_1
    user_id2 = "test_user_2" # 李四
    user_id3 = "test_user_3" # 王五 (机器人)

    # 添加更多模拟用户到 mock_person_info_manager 和 mock_relationship_manager
    mock_person_info_manager.add_mock_user(platform, user_id2, "pid_test_user_2")
    mock_relationship_manager.add_mock_user_name(platform, user_id2, "李四")
    
    mock_person_info_manager.add_mock_user(platform, user_id3, "pid_bot_user") # 假设这是机器人
    mock_relationship_manager.add_mock_user_name(platform, user_id3, global_config.bot.nickname) # 机器人昵称

    # --- 3. 模拟聊天场景和绰号分析 ---
    stream_id1 = f"{platform}-{group_id1}"
    chat_stream1 = MockChatStream(stream_id1, platform, group_id1)
    chat_streams_history[stream_id1] = chat_stream1 # 将流添加到全局历史记录

    # 模拟一些聊天消息
    chat_stream1.add_message(user_id1, "大家好，我是张三！")
    await asyncio.sleep(0.1) # 确保时间戳不同
    chat_stream1.add_message(user_id2, f"你好张三，我是李四。听说 {user_id1} 也叫“老张”？") # 李四提到了张三的绰号
    await asyncio.sleep(0.1)
    
    # 机器人回复 (这条消息是 anchor_message，触发分析)
    bot_reply_text = [f"明白了，李四。我会记住“老张”这个称呼的。"]
    anchor_msg_user2 = MockMessageRecv(chat_stream1, user_id2, chat_stream1.messages[-1]['message_content'])


    # 设置模拟LLM的响应
    # 我们期望LLM从 "听说 test_user_1 也叫“老张”？" 中提取 "test_user_1": "老张"
    # 构建一个基于 prompt 内容的标识符，例如包含 "test_user_1" 和 "老张"
    llm_response_for_user1_laozhang = {
        "is_exist": True,
        "data": {
            user_id1: "老张" # LLM 应该返回 platform_user_id
        }
    }
    # SobriquetManager._call_llm_for_analysis 会构建 prompt，我们需要确保我们的 identifier 能匹配到
    # 一个简单的方法是基于 user_id1
    set_mock_llm_response(identifier=f"ID: {user_id1}", response_json_string=json.dumps(llm_response_for_user1_laozhang))
    logger.info(f"为用户 {user_id1} 设置了模拟 LLM 响应，期望绰号 '老张'")


    # 触发绰号分析
    logger.info(f"触发对用户 {user_id2} 消息的绰号分析...")
    await sobriquet_manager.trigger_sobriquet_analysis(anchor_msg_user2, bot_reply_text)
    
    # 给后台线程一些时间处理队列中的任务
    logger.info("等待后台绰号处理 (2秒)...")
    await asyncio.sleep(2) 

    # --- 4. 验证数据库中的绰号 ---
    logger.info("验证数据库中的绰号...")
    # 需要一种方式从 SobriquetDB 读取数据进行验证
    # 例如，添加一个 get_sobriquets_for_user_group(profile_doc_id, group_key) 到 SobriquetDB
    # 或者直接查询 SQLite 文件 (为了测试简单，可以这样做)
    
    # 首先获取 profile_document_id for user_id1
    pid_user1 = mock_person_info_manager.get_person_id(platform, user_id1)
    if pid_user1:
        profile_doc_id_user1 = profile_manager.generate_profile_document_id(pid_user1)
        
        # (这是一个简化的验证，实际中 SobriquetDB 应提供查询方法)
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT sobriquets_by_group FROM profile_info WHERE _id = ?", (profile_doc_id_user1,))
        row = cursor.fetchone()
        conn.close()

        if row and row[0]:
            sobriquets_data = json.loads(row[0])
            group_key = f"{platform}-{group_id1}"
            user1_group_sobriquets = sobriquets_data.get(group_key, {}).get("sobriquets", [])
            found_laozhang = any(s.get("name") == "老张" and s.get("count", 0) > 0 for s in user1_group_sobriquets)
            if found_laozhang:
                logger.info(f"成功: 在数据库中为用户 {user_id1} 找到了绰号 '老张'。数据: {user1_group_sobriquets}")
            else:
                logger.error(f"失败: 未在数据库中为用户 {user_id1} 找到绰号 '老张'。数据库数据: {user1_group_sobriquets}")
        else:
            logger.error(f"失败: 未在数据库中找到用户 {user_id1} (profile_doc_id: {profile_doc_id_user1}) 的绰号数据。")
    else:
        logger.error(f"失败: 无法获取用户 {user_id1} 的 person_info_pid。")


    # --- 5. 测试绰号注入功能 ---
    logger.info("测试绰号注入功能...")
    # 假设现在有新消息，需要为 prompt 注入绰号信息
    # message_list_before_now 应该包含最近的聊天记录
    # 我们用 chat_stream1.messages 作为模拟
    
    # 确保 user_id1 (张三) 和 user_id2 (李四) 都在上下文中
    context_messages_for_injection = [
        {"user_info": {"user_id": user_id1}, "message_content": "...", "timestamp": time.time() - 10},
        {"user_info": {"user_id": user_id2}, "message_content": "...", "timestamp": time.time() - 5}
    ]

    injection_string = await sobriquet_manager.get_sobriquet_prompt_injection(chat_stream1, context_messages_for_injection)
    if injection_string:
        logger.info(f"获取到的绰号注入字符串:\n{injection_string.strip()}")
        if "张三" in injection_string and "老张" in injection_string and user_id1 in injection_string:
            logger.info("成功: 绰号注入字符串包含了预期的用户信息和绰号。")
        else:
            logger.error(f"失败: 绰号注入字符串未包含预期的用户信息或绰号。内容: {injection_string}")
    else:
        logger.warning("未能获取到绰号注入字符串 (可能因为上下文中没有用户或没有绰号数据)。")
    
    # --- 6. 模拟另一轮对话，李四获得绰号 "老李" ---
    chat_stream1.add_message(user_id1, f"对了，{user_id2}，大家都叫你“老李”吗？") # 张三提到了李四的绰号
    await asyncio.sleep(0.1)
    bot_reply_text_2 = ["好的，张三。"]
    anchor_msg_user1 = MockMessageRecv(chat_stream1, user_id1, chat_stream1.messages[-1]['message_content'])

    llm_response_for_user2_laoli = {
        "is_exist": True,
        "data": {
            user_id2: "老李"
        }
    }
    set_mock_llm_response(identifier=f"ID: {user_id2}", response_json_string=json.dumps(llm_response_for_user2_laoli))
    logger.info(f"为用户 {user_id2} 设置了模拟 LLM 响应，期望绰号 '老李'")

    logger.info(f"触发对用户 {user_id1} 消息的绰号分析 (关于李四的绰号)...")
    await sobriquet_manager.trigger_sobriquet_analysis(anchor_msg_user1, bot_reply_text_2)
    
    logger.info("等待后台绰号处理 (2秒)...")
    await asyncio.sleep(2)

    # 验证李四的绰号
    pid_user2 = mock_person_info_manager.get_person_id(platform, user_id2)
    if pid_user2:
        profile_doc_id_user2 = profile_manager.generate_profile_document_id(pid_user2)
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.execute("SELECT sobriquets_by_group FROM profile_info WHERE _id = ?", (profile_doc_id_user2,))
        row_user2 = cursor.fetchone()
        conn.close()

        if row_user2 and row_user2[0]:
            sobriquets_data_u2 = json.loads(row_user2[0])
            user2_group_sobriquets = sobriquets_data_u2.get(f"{platform}-{group_id1}", {}).get("sobriquets", [])
            found_laoli = any(s.get("name") == "老李" and s.get("count", 0) > 0 for s in user2_group_sobriquets)
            if found_laoli:
                logger.info(f"成功: 在数据库中为用户 {user_id2} 找到了绰号 '老李'。")
            else:
                logger.error(f"失败: 未在数据库中为用户 {user_id2} 找到绰号 '老李'。数据: {user2_group_sobriquets}")
        else:
            logger.error(f"失败: 未在数据库中找到用户 {user_id2} (profile_doc_id: {profile_doc_id_user2}) 的绰号数据。")


    # --- 7. 清理 ---
    logger.info("停止 SobriquetManager 处理器...")
    sobriquet_manager.stop_processor()
    # 等待线程完全停止
    await asyncio.sleep(1) 
    logger.info("测试场景结束。")


if __name__ == "__main__":
    # 配置日志记录器
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # 可以将特定模块的日志级别调低，例如 asyncio 的日志
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    asyncio.run(run_test_scenario())

    # 检查是否有未完成的 asyncio 任务 (调试用)
    # tasks = [t for t in asyncio.all_tasks() if not t.done()]
    # if tasks:
    #     logger.warning(f"发现 {len(tasks)} 个未完成的 asyncio 任务:")
    #     for task in tasks:
    #         logger.warning(task)
