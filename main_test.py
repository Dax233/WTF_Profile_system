# main_test.py
import asyncio
import time
import os
import json # 用于设置模拟LLM响应
import sqlite3
import logging

# 导入修改后的管理器和数据库处理器
from profile.profile_db import ProfileDB
from profile.profile_manager import ProfileManager
from profile.sobriquet.sobriquet_manager import SobriquetManager

from typing import Dict, List, Optional, Any, Tuple # 根据实际用到的添加
# 导入模拟依赖
from stubs.mock_config import global_config
from stubs.mock_dependencies import (
    get_logger,
    person_info_manager as mock_person_info_manager, # 使用模拟实例
    relationship_manager as mock_relationship_manager, # 使用模拟实例
    set_mock_llm_response, # 用于设置LLM响应
    MockChatStream,
    MockMessageRecv,
)

logger = get_logger("MainTest")

chat_streams_history: Dict[str, MockChatStream] = {}


async def run_test_scenario():
    logger.info("开始测试场景...")
    logger.debug(f"main_test.py: Initial id(chat_streams_history): {id(chat_streams_history)}")


    db_file = global_config.profile.db_path
    # if os.path.exists(db_file):
    #     logger.info(f"删除旧的数据库文件: {db_file}")
    #     os.remove(db_file)

    sobriquet_db = ProfileDB(db_path=db_file)
    logger.info(f"SobriquetDB (SQLite) 初始化完成，数据库文件: {sobriquet_db.db_path}")

    profile_manager = ProfileManager(sobriquet_db_instance=sobriquet_db)
    logger.info("ProfileManager (SQLite) 初始化完成。")

    sobriquet_manager = SobriquetManager(
        db_handler=sobriquet_db, 
        profile_manager_instance=profile_manager,
        chat_history_provider=chat_streams_history 
    )
    logger.info("SobriquetManager (SQLite, MockLLM) 初始化完成。")
    logger.debug(f"main_test.py: id(sobriquet_manager.chat_history_provider): {id(sobriquet_manager.chat_history_provider)}")


    sobriquet_manager.start_processor()
    logger.info("SobriquetManager 处理器已启动。")

    platform = "test_platform"
    group_id1 = "group101"
    user_id1 = "test_user_1" 
    user_id2 = "test_user_2" 
    user_id3 = "test_user_3" # Bot

    mock_person_info_manager.add_mock_user(platform, user_id2, "pid_test_user_2")
    mock_relationship_manager.add_mock_user_name(platform, user_id2, "李四")
    
    mock_person_info_manager.add_mock_user(platform, user_id3, "pid_bot_user") 
    mock_relationship_manager.add_mock_user_name(platform, user_id3, global_config.bot.nickname) 

    stream_id1 = f"{platform}-{group_id1}"
    chat_stream1 = MockChatStream(stream_id1, platform, group_id1)
    chat_streams_history[stream_id1] = chat_stream1 
    logger.debug(f"main_test.py: Populated chat_streams_history with key '{stream_id1}'. Current keys: {list(chat_streams_history.keys())}")


    chat_stream1.add_message(user_id1, "大家好，我是张三！")
    await asyncio.sleep(0.1) 
    chat_stream1.add_message(user_id2, f"你好张三，我是李四。听说 {user_id1} 也叫“阿张”？") 
    await asyncio.sleep(0.1)
    
    bot_reply_text = [f"明白了，李四。我会记住“老张”这个称呼的。"]
    anchor_msg_user2 = MockMessageRecv(chat_stream1, user_id2, chat_stream1.messages[-1]['message_content'])

    llm_response_for_user1_laozhang = {
        "is_exist": True,
        "data": {
            user_id1: "阿张" 
        }
    }
    set_mock_llm_response(identifier=user_id1, response_json_string=json.dumps(llm_response_for_user1_laozhang))
    logger.info(f"为用户 {user_id1} 设置了模拟 LLM 响应 (identifier: '{user_id1}'), 期望绰号 '老张'")

    logger.info(f"触发对用户 {user_id2} 消息的绰号分析...")
    await sobriquet_manager.trigger_sobriquet_analysis(anchor_msg_user2, bot_reply_text)
    
    logger.info("等待后台绰号处理 (3秒)...") 
    await asyncio.sleep(3) 

    logger.info("验证数据库中的绰号 (场景1: 老张)...") # 更明确的日志
    pid_user1 = mock_person_info_manager.get_person_id(platform, user_id1)
    if pid_user1:
        profile_doc_id_user1 = profile_manager.generate_profile_document_id(pid_user1)
        
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
                logger.info(f"成功 (场景1): 在数据库中为用户 {user_id1} 找到了绰号 '老张'。数据: {user1_group_sobriquets}")
            else:
                logger.error(f"失败 (场景1): 未在数据库中为用户 {user_id1} 找到绰号 '老张'。数据库数据: {user1_group_sobriquets}")
        else:
            logger.error(f"失败 (场景1): 未在数据库中找到用户 {user_id1} (profile_doc_id: {profile_doc_id_user1}) 的绰号数据。")
    else:
        logger.error(f"失败 (场景1): 无法获取用户 {user_id1} 的 person_info_pid。")

    logger.info("测试绰号注入功能 (场景1后)...") # 更明确的日志
    context_messages_for_injection = [
        {"user_info": {"user_id": user_id1}, "message_content": "...", "timestamp": time.time() - 10},
        {"user_info": {"user_id": user_id2}, "message_content": "...", "timestamp": time.time() - 5}
    ]

    injection_string = await sobriquet_manager.get_sobriquet_prompt_injection(chat_stream1, context_messages_for_injection)
    if injection_string:
        logger.info(f"获取到的绰号注入字符串 (场景1后):\n{injection_string.strip()}")
        # "测试用户一" 是 user_id1 (张三) 在 mock_relationship_manager 中的名字
        if "测试用户一" in injection_string and "老张" in injection_string and user_id1 in injection_string:
            logger.info("成功 (场景1后): 绰号注入字符串包含了预期的用户信息和绰号。")
        else:
            logger.error(f"失败 (场景1后): 绰号注入字符串未包含预期的用户信息或绰号。内容: {injection_string}")
    else:
        logger.warning("未能获取到绰号注入字符串 (场景1后) (可能因为上下文中没有用户或没有绰号数据)。")
    
    # --- 场景 2: 李四获得绰号 "老李" ---
    logger.info("-" * 30 + " 开始场景 2: 李四 ('老李') " + "-" * 30)
    chat_stream1.add_message(user_id1, f"对了，{user_id2}，大家都叫你“老李”吗？") 
    await asyncio.sleep(0.1)
    bot_reply_text_2 = ["好的，张三。"]
    anchor_msg_user1_for_scene2 = MockMessageRecv(chat_stream1, user_id1, chat_stream1.messages[-1]['message_content'])

    llm_response_for_user2_laoli = {
        "is_exist": True,
        "data": {
            user_id2: "老李" # LLM 应该返回 platform_user_id (user_id2) 和对应的绰号 "老李"
        }
    }
    # --- 关键修正点 ---
    # 确保 user_id2 的 identifier 是直接使用 user_id2
    set_mock_llm_response(identifier=user_id2, response_json_string=json.dumps(llm_response_for_user2_laoli))
    logger.info(f"为用户 {user_id2} 设置了模拟 LLM 响应 (identifier: '{user_id2}'), 期望绰号 '老李'")

    logger.info(f"触发对用户 {user_id1} 消息的绰号分析 (场景2: 关于李四的绰号)...")
    await sobriquet_manager.trigger_sobriquet_analysis(anchor_msg_user1_for_scene2, bot_reply_text_2)
    
    logger.info("等待后台绰号处理 (场景2, 3秒)...") 
    await asyncio.sleep(3)

    logger.info("验证数据库中的绰号 (场景2: 老李)...") # 更明确的日志
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
                logger.info(f"成功 (场景2): 在数据库中为用户 {user_id2} 找到了绰号 '老李'。数据: {user2_group_sobriquets}")
            else:
                logger.error(f"失败 (场景2): 未在数据库中为用户 {user_id2} 找到绰号 '老李'。数据库数据: {user2_group_sobriquets}")
        else:
            logger.error(f"失败 (场景2): 未在数据库中找到用户 {user_id2} (profile_doc_id: {profile_doc_id_user2}) 的绰号数据。")
    else:
        logger.error(f"失败 (场景2): 无法获取用户 {user_id2} 的 person_info_pid。")


    logger.info("停止 SobriquetManager 处理器...")
    sobriquet_manager.stop_processor()
    await asyncio.sleep(1) 
    logger.info("测试场景结束。")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logging.getLogger("asyncio").setLevel(logging.INFO) 
    
    asyncio.run(run_test_scenario())
