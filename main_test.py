# main_test.py
import asyncio
import time
import os
import json # 用于设置模拟LLM响应
import sqlite3 # 用于直接查询验证
import logging

# 导入修改后的管理器和数据库处理器
from profile.profile_db import ProfileDB 
from profile.profile_manager import ProfileManager
from profile.sobriquet.sobriquet_manager import SobriquetManager

from typing import Dict, List, Optional, Any, Tuple 
# 导入模拟依赖
from stubs.mock_config import global_config
from stubs.mock_dependencies import (
    get_logger,
    person_info_manager as mock_person_info_manager, 
    relationship_manager as mock_relationship_manager, 
    set_mock_llm_response, 
    MockChatStream,
    MockMessageRecv,
)

logger = get_logger("MainTest")

chat_streams_history: Dict[str, MockChatStream] = {}


async def run_test_scenario():
    logger.info("开始测试场景...")
    logger.debug(f"main_test.py: Initial id(chat_streams_history): {id(chat_streams_history)}")


    db_file = global_config.profile.db_path
    if os.path.exists(db_file):
        logger.info(f"删除旧的数据库文件: {db_file}")
        os.remove(db_file)

    profile_db_instance = ProfileDB(db_path=db_file) 
    logger.info(f"ProfileDB (SQLite) 初始化完成，数据库文件: {profile_db_instance.db_path}")

    profile_manager_instance = ProfileManager(profile_db_instance=profile_db_instance) 
    logger.info("ProfileManager (SQLite) 初始化完成。")

    sobriquet_manager_instance = SobriquetManager(
        db_handler=profile_db_instance, 
        profile_manager_instance=profile_manager_instance,
        chat_history_provider=chat_streams_history 
    )
    logger.info("SobriquetManager (SQLite, MockLLM) 初始化完成。")
    logger.debug(f"main_test.py: id(sobriquet_manager.chat_history_provider): {id(sobriquet_manager_instance.chat_history_provider)}")


    sobriquet_manager_instance.start_processor()
    logger.info("SobriquetManager 处理器已启动。")

    platform = "test_platform"
    group_id1 = "group101"
    group_id2 = "group102" 
    user_id1 = "test_user_1" 
    user_id2 = "test_user_2" 
    user_id3 = "test_user_3" # Bot

    mock_person_info_manager.add_mock_user(platform, user_id1, "pid_test_user_1") 
    mock_relationship_manager.add_mock_user_name(platform, user_id1, "张三")

    mock_person_info_manager.add_mock_user(platform, user_id2, "pid_test_user_2")
    mock_relationship_manager.add_mock_user_name(platform, user_id2, "李四")
    
    mock_person_info_manager.add_mock_user(platform, user_id3, "pid_bot_user") 
    mock_relationship_manager.add_mock_user_name(platform, user_id3, global_config.bot.nickname) 

    pid_user1 = mock_person_info_manager.get_person_id(platform, user_id1)
    npid_user1 = profile_manager_instance.generate_profile_document_id(pid_user1) if pid_user1 else None

    pid_user2 = mock_person_info_manager.get_person_id(platform, user_id2)
    npid_user2 = profile_manager_instance.generate_profile_document_id(pid_user2) if pid_user2 else None

    stream_id1 = f"{platform}-{group_id1}"
    chat_stream1 = MockChatStream(stream_id1, platform, group_id1)
    chat_streams_history[stream_id1] = chat_stream1 
    logger.debug(f"main_test.py: Populated chat_streams_history with key '{stream_id1}'. Current keys: {list(chat_streams_history.keys())}")

    chat_stream1.add_message(user_id1, "大家好，我是张三！")
    await asyncio.sleep(0.01) 
    chat_stream1.add_message(user_id2, f"你好张三，我是李四。听说 {user_id1} 也叫“老张”？") 
    await asyncio.sleep(0.01)
    
    bot_reply_text = [f"明白了，李四。我会记住“老张”这个称呼的。"]
    anchor_msg_user2 = MockMessageRecv(chat_stream1, user_id2, chat_stream1.messages[-1]['message_content'])

    llm_response_for_user1_laozhang = {
        "is_exist": True,
        "data": {
            user_id1: "老张" 
        }
    }
    set_mock_llm_response(identifier=user_id1, response_json_string=json.dumps(llm_response_for_user1_laozhang))
    logger.info(f"为用户 {user_id1} 设置了模拟 LLM 响应 (identifier: '{user_id1}'), 期望绰号 '老张'")

    logger.info(f"触发对用户 {user_id2} 消息的绰号分析...")
    await sobriquet_manager_instance.trigger_sobriquet_analysis(anchor_msg_user2, bot_reply_text, chat_stream=chat_stream1)
    
    # --- 增加等待时间以确保后台数据库操作完成 ---
    logger.info("等待后台绰号处理 (场景1, 3秒)...") 
    await asyncio.sleep(3) 

    logger.info("验证数据库中的绰号 (场景1: 老张)...")
    if npid_user1:
        profile_doc_user1_after_scene1 = await profile_db_instance.get_profile_document(npid_user1)
        if profile_doc_user1_after_scene1 and profile_doc_user1_after_scene1.get("sobriquets_by_group"):
            sobriquets_data = profile_doc_user1_after_scene1["sobriquets_by_group"]
            group_key = f"{platform}-{group_id1}"
            user1_group_sobriquets = sobriquets_data.get(group_key, {}).get("sobriquets", [])
            found_laozhang = any(s.get("name") == "老张" and s.get("count", 0) > 0 for s in user1_group_sobriquets)
            if found_laozhang:
                logger.info(f"成功 (场景1): 在数据库中为用户 {user_id1} (NPID: {npid_user1}) 找到了绰号 '老张'。数据: {user1_group_sobriquets}")
            else:
                logger.error(f"失败 (场景1): 未在数据库中为用户 {user_id1} (NPID: {npid_user1}) 找到绰号 '老张'。数据库数据: {user1_group_sobriquets}")
        else:
            logger.error(f"失败 (场景1): 未在数据库中找到用户 {user_id1} (NPID: {npid_user1}) 的绰号数据。文档: {profile_doc_user1_after_scene1}")
    else:
        logger.error(f"失败 (场景1): 无法获取用户 {user_id1} 的 NaturalPersonID。")

    logger.info("-" * 30 + " 测试 get_profile_data_for_prompt (场景1后) " + "-" * 30)
    if npid_user1:
        platform_user_id_to_npid_map_scene1 = {user_id1: npid_user1}
        prompt_export_data_scene1 = await profile_manager_instance.get_profile_data_for_prompt(
            natural_person_ids_in_context=[npid_user1],
            current_platform=platform,
            platform_user_id_to_npid_map=platform_user_id_to_npid_map_scene1,
            current_group_id=group_id1
        )
        logger.debug(f"Prompt export data (场景1后) for NPID {npid_user1}: \n{json.dumps(prompt_export_data_scene1.get(npid_user1), indent=2, ensure_ascii=False)}")
        
        user1_exported_profile = prompt_export_data_scene1.get(npid_user1)
        if user1_exported_profile:
            ctx = user1_exported_profile.get("current_platform_context", {}).get(platform, {}).get(user_id1, {})
            if ctx.get("group_nickname") == "老张" and ctx.get("platform_nickname") == "张三":
                logger.info("成功 (Prompt Export): current_platform_context 验证通过 ('老张', '张三')。")
            else:
                logger.error(f"失败 (Prompt Export): current_platform_context 验证失败。得到: {ctx}")

            summary = user1_exported_profile.get("all_known_sobriquets_summary", "")
            if "老张" in summary: # 验证是否包含 "老张"
                logger.info(f"成功 (Prompt Export): all_known_sobriquets_summary 包含 '老张'。摘要: {summary}")
            else:
                logger.error(f"失败 (Prompt Export): all_known_sobriquets_summary 未包含 '老张'。摘要: {summary}")
            
            if ("identity" in user1_exported_profile and "personality" in user1_exported_profile and "impression" in user1_exported_profile):
                 logger.info("成功 (Prompt Export): identity, personality, impression 字段存在。")
            else:
                 logger.error(f"失败 (Prompt Export): identity, personality, impression 字段部分或全部缺失。 Profile: {user1_exported_profile.keys()}")
        else:
            logger.error(f"失败 (Prompt Export): 未能为 NPID {npid_user1} 获取导出的画像数据。")

    logger.info("-" * 30 + " 开始场景 2: 李四 ('老李') " + "-" * 30)
    if npid_user2:
        # 确保文档存在，并为 user_id2 (李四) 添加另一个群组的绰号，以测试 all_known_sobriquets_summary
        await profile_db_instance.ensure_profile_document_exists(npid_user2, pid_user2, platform, user_id2)
        await profile_db_instance.update_group_sobriquet_count(npid_user2, platform, group_id2, "李哥") 
        logger.info(f"为 NPID {npid_user2} 在群组 {group_id2} 添加了初始绰号 '李哥'")

    chat_stream1.add_message(user_id1, f"对了，{user_id2}，大家都叫你“老李”吗？") 
    await asyncio.sleep(0.01)
    bot_reply_text_2 = ["好的，张三。"]
    anchor_msg_user1_for_scene2 = MockMessageRecv(chat_stream1, user_id1, chat_stream1.messages[-1]['message_content'])

    llm_response_for_user2_laoli = {
        "is_exist": True,
        "data": {
            user_id2: "老李" 
        }
    }
    set_mock_llm_response(identifier=user_id2, response_json_string=json.dumps(llm_response_for_user2_laoli))
    logger.info(f"为用户 {user_id2} 设置了模拟 LLM 响应 (identifier: '{user_id2}'), 期望绰号 '老李'")

    logger.info(f"触发对用户 {user_id1} 消息的绰号分析 (场景2: 关于李四的绰号)...")
    await sobriquet_manager_instance.trigger_sobriquet_analysis(anchor_msg_user1_for_scene2, bot_reply_text_2, chat_stream=chat_stream1)
    
    # --- 增加等待时间 ---
    logger.info("等待后台绰号处理 (场景2, 3秒)...") 
    await asyncio.sleep(3)

    logger.info("验证数据库中的绰号 (场景2: 老李)...") 
    if npid_user2:
        profile_doc_user2_after_scene2 = await profile_db_instance.get_profile_document(npid_user2)
        if profile_doc_user2_after_scene2 and profile_doc_user2_after_scene2.get("sobriquets_by_group"):
            sobriquets_data_u2 = profile_doc_user2_after_scene2["sobriquets_by_group"]
            group_key_scene2 = f"{platform}-{group_id1}" 
            user2_group_sobriquets_scene2 = sobriquets_data_u2.get(group_key_scene2, {}).get("sobriquets", [])
            found_laoli = any(s.get("name") == "老李" and s.get("count", 0) > 0 for s in user2_group_sobriquets_scene2)
            if found_laoli:
                logger.info(f"成功 (场景2): 在数据库中为用户 {user_id2} (NPID: {npid_user2}) 在群组 {group_id1} 找到了绰号 '老李'。")
            else:
                logger.error(f"失败 (场景2): 未在数据库中为用户 {user_id2} (NPID: {npid_user2}) 在群组 {group_id1} 找到绰号 '老李'。数据: {user2_group_sobriquets_scene2}")
        else:
            logger.error(f"失败 (场景2): 未在数据库中找到用户 {user_id2} (NPID: {npid_user2}) 的绰号数据。文档: {profile_doc_user2_after_scene2}")
    else:
        logger.error(f"失败 (场景2): 无法获取用户 {user_id2} 的 NaturalPersonID。")

    logger.info("-" * 30 + " 测试 get_profile_data_for_prompt (场景2后) " + "-" * 30)
    if npid_user1 and npid_user2:
        platform_user_id_to_npid_map_scene2 = {user_id1: npid_user1, user_id2: npid_user2}
        prompt_export_data_scene2 = await profile_manager_instance.get_profile_data_for_prompt(
            natural_person_ids_in_context=[npid_user1, npid_user2], 
            current_platform=platform,
            platform_user_id_to_npid_map=platform_user_id_to_npid_map_scene2,
            current_group_id=group_id1 # 当前上下文在 group101
        )
        
        user2_exported_profile = prompt_export_data_scene2.get(npid_user2)
        if user2_exported_profile:
            logger.debug(f"Prompt export data (场景2后) for NPID {npid_user2}: \n{json.dumps(user2_exported_profile, indent=2, ensure_ascii=False)}")
            ctx_u2 = user2_exported_profile.get("current_platform_context", {}).get(platform, {}).get(user_id2, {})
            if ctx_u2.get("group_nickname") == "老李" and ctx_u2.get("platform_nickname") == "李四": 
                logger.info("成功 (Prompt Export - user2): current_platform_context 验证通过 ('老李', '李四')。")
            else:
                logger.error(f"失败 (Prompt Export - user2): current_platform_context 验证失败。得到: {ctx_u2}")

            summary_u2 = user2_exported_profile.get("all_known_sobriquets_summary", "")
            # --- 修正断言：all_known_sobriquets_summary 在 group_id1 上下文中只应包含 "老李" ---
            # "李哥" 是在 group_id2 中的，不应该出现在 current_group_id=group_id1 的摘要中
            if "老李" in summary_u2 and "李哥" not in summary_u2: 
                logger.info(f"成功 (Prompt Export - user2): all_known_sobriquets_summary 验证通过 (只含 '老李')。摘要: {summary_u2}")
            else:
                logger.error(f"失败 (Prompt Export - user2): all_known_sobriquets_summary 验证失败。应只含 '老李'，不含 '李哥'。摘要: {summary_u2}")
        else:
            logger.error(f"失败 (Prompt Export - user2): 未能为 NPID {npid_user2} 获取导出的画像数据。")

    logger.info("停止 SobriquetManager 处理器...")
    sobriquet_manager_instance.stop_processor() 
    await asyncio.sleep(0.1) 
    logger.info("测试场景结束。")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logging.getLogger("asyncio").setLevel(logging.INFO) 
    
    asyncio.run(run_test_scenario())
