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


async def run_sobriquet_test_scenario(profile_db_instance: ProfileDB, profile_manager_instance: ProfileManager):
    logger.info("开始绰号功能测试场景...")
    # chat_streams_history 在外部定义并传入，或者在这里管理也可以
    # 为了隔离，这里的 chat_streams_history 可以是局部的，或者确保清理
    
    # SobriquetManager 依赖 chat_streams_history
    # 注意：如果 chat_streams_history 是全局的，确保它在不同测试场景间得到正确管理或重置
    current_test_chat_streams_history: Dict[str, MockChatStream] = {}


    sobriquet_manager_instance = SobriquetManager(
        db_handler=profile_db_instance, 
        profile_manager_instance=profile_manager_instance,
        chat_history_provider=current_test_chat_streams_history # 使用当前测试场景的聊天记录
    )
    logger.info("SobriquetManager (SQLite, MockLLM) 初始化完成。")
    
    sobriquet_manager_instance.start_processor()
    logger.info("SobriquetManager 处理器已启动。")

    platform = "test_platform_sobriquet" # 使用不同的平台以避免与其他测试冲突
    group_id1 = "group_sob_101"
    group_id2 = "group_sob_102" 
    user_id1 = "sob_user_1" 
    user_id2 = "sob_user_2" 
    user_id3 = "sob_user_bot" # Bot

    # 为模拟依赖准备数据
    mock_person_info_manager.add_mock_user(platform, user_id1, "pid_sob_user_1") 
    mock_relationship_manager.add_mock_user_name(platform, user_id1, "张三")

    mock_person_info_manager.add_mock_user(platform, user_id2, "pid_sob_user_2")
    mock_relationship_manager.add_mock_user_name(platform, user_id2, "李四")
    
    mock_person_info_manager.add_mock_user(platform, user_id3, "pid_sob_bot_user") 
    mock_relationship_manager.add_mock_user_name(platform, user_id3, global_config.bot.nickname) 

    pid_user1 = mock_person_info_manager.get_person_id(platform, user_id1)
    npid_user1 = profile_manager_instance.generate_profile_document_id(pid_user1) if pid_user1 else None

    pid_user2 = mock_person_info_manager.get_person_id(platform, user_id2)
    npid_user2 = profile_manager_instance.generate_profile_document_id(pid_user2) if pid_user2 else None

    stream_id1 = f"{platform}-{group_id1}"
    chat_stream1 = MockChatStream(stream_id1, platform, group_id1)
    current_test_chat_streams_history[stream_id1] = chat_stream1
    logger.debug(f"SobriquetTest: Populated chat_streams_history with key '{stream_id1}'.")

    chat_stream1.add_message(user_id1, "大家好，我是张三！")
    await asyncio.sleep(0.01) 
    chat_stream1.add_message(user_id2, f"你好张三，我是李四。听说 {user_id1} 也叫“老张”？") 
    await asyncio.sleep(0.01)
    
    bot_reply_text = [f"明白了，李四。我会记住“老张”这个称呼的。"]
    # 模拟 MessageRecv 对象
    # 假设 MockMessageRecv 的构造函数是 (chat_stream, user_id, text_content)
    # 并且它能从 chat_stream 中获取 platform, group_id 等信息
    # 或者，如果 trigger_sobriquet_analysis 需要更完整的 MessageRecv 对象，
    # 我们可能需要更细致地模拟它或调整 trigger_sobriquet_analysis 的接口
    anchor_msg_user2 = MockMessageRecv(chat_stream1, user_id2, chat_stream1.messages[-1]['message_content'])


    llm_response_for_user1_laozhang = {
        "is_exist": True,
        "data": {
            user_id1: "老张" # LLM返回的是 platform_user_id
        }
    }
    # 使用一个独特的标识符来设置模拟响应，这个标识符应该能在LLM的prompt中被找到
    # 在当前的 sobriquet_mapper.py 中，prompt 会包含聊天记录中的 user_id
    # 所以，我们可以用目标用户的 user_id 作为 set_mock_llm_response 的 identifier
    set_mock_llm_response(identifier=user_id1, response_json_string=json.dumps(llm_response_for_user1_laozhang))
    logger.info(f"为用户 {user_id1} 设置了模拟 LLM 响应 (identifier: '{user_id1}'), 期望绰号 '老张'")

    logger.info(f"触发对用户 {user_id2} 消息的绰号分析...")
    await sobriquet_manager_instance.trigger_sobriquet_analysis(anchor_msg_user2, bot_reply_text, chat_stream=chat_stream1)
    
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
                logger.debug(f"完整文档 (场景1 User1): {profile_doc_user1_after_scene1}")

        else:
            logger.error(f"失败 (场景1): 未在数据库中找到用户 {user_id1} (NPID: {npid_user1}) 的绰号数据。文档: {profile_doc_user1_after_scene1}")
    else:
        logger.error(f"失败 (场景1): 无法获取用户 {user_id1} 的 NaturalPersonID。")

    logger.info("-" * 30 + " 测试 get_profile_data_for_prompt (场景1后) " + "-" * 30)
    if npid_user1:
        # 构建 platform_user_id 到 npid 的映射，仅包含当前上下文中的用户
        platform_user_id_to_npid_map_scene1 = {user_id1: npid_user1}
        prompt_export_data_scene1 = await profile_manager_instance.get_profile_data_for_prompt(
            natural_person_ids_in_context=[npid_user1], # 只导出 user1 的画像
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
            if "老张" in summary: 
                logger.info(f"成功 (Prompt Export): all_known_sobriquets_summary 包含 '老张'。摘要: {summary}")
            else:
                logger.error(f"失败 (Prompt Export): all_known_sobriquets_summary 未包含 '老张'。摘要: {summary}")
            
            # 检查 identity 和 personality 字段是否存在 (即使它们是空的)
            if ("identity" in user1_exported_profile and 
                "personality" in user1_exported_profile and
                "impression" in user1_exported_profile): # impression 也是默认字段
                 logger.info("成功 (Prompt Export): identity, personality, impression 字段存在。")
            else:
                 logger.error(f"失败 (Prompt Export): identity, personality, impression 字段部分或全部缺失。 Profile keys: {list(user1_exported_profile.keys())}")
        else:
            logger.error(f"失败 (Prompt Export): 未能为 NPID {npid_user1} 获取导出的画像数据。")


    logger.info("-" * 30 + " 开始场景 2: 李四 ('老李') " + "-" * 30)
    if npid_user2:
        # 确保文档存在，并为 user_id2 (李四) 添加另一个群组的绰号，以测试 all_known_sobriquets_summary
        # 注意：ensure_profile_document_exists 现在也处理平台账户
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
            current_group_id=group_id1 
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
            if "老李" in summary_u2 and "李哥" not in summary_u2: 
                logger.info(f"成功 (Prompt Export - user2): all_known_sobriquets_summary 验证通过 (只含 '老李')。摘要: {summary_u2}")
            else:
                logger.error(f"失败 (Prompt Export - user2): all_known_sobriquets_summary 验证失败。应只含 '老李'，不含 '李哥'。摘要: {summary_u2}")
        else:
            logger.error(f"失败 (Prompt Export - user2): 未能为 NPID {npid_user2} 获取导出的画像数据。")

    logger.info("停止 SobriquetManager 处理器...")
    sobriquet_manager_instance.stop_processor() 
    await asyncio.sleep(0.1) 
    logger.info("绰号功能测试场景结束。")


async def run_platform_accounts_test_scenario(profile_db_instance: ProfileDB, profile_manager_instance: ProfileManager):
    logger.info("=" * 30 + " 开始 Platform Accounts 测试场景 " + "=" * 30)

    # 定义测试用户和平台
    # 用户A (pid_pa_A) -> npid_A
    #   - platform_X, user_A_px1
    #   - platform_Y, user_A_py1
    # 用户B (pid_pa_B) -> npid_B
    #   - platform_X, user_B_px1
    #   - platform_Z, user_B_pz1
    # 用户C (pid_pa_C) -> npid_C (同一个人，多平台多账户)
    #   - platform_X, user_C_px1
    #   - platform_Y, user_C_py1
    #   - platform_X, user_C_px2 (同一平台不同账号，但应关联到 pid_pa_C)

    # 模拟 person_info_manager 返回的 PID
    mock_person_info_manager.add_mock_user("platform_X", "user_A_px1", "pid_pa_A")
    mock_person_info_manager.add_mock_user("platform_Y", "user_A_py1", "pid_pa_A")

    mock_person_info_manager.add_mock_user("platform_X", "user_B_px1", "pid_pa_B")
    mock_person_info_manager.add_mock_user("platform_Z", "user_B_pz1", "pid_pa_B")
    
    mock_person_info_manager.add_mock_user("platform_X", "user_C_px1", "pid_pa_C")
    mock_person_info_manager.add_mock_user("platform_Y", "user_C_py1", "pid_pa_C")
    mock_person_info_manager.add_mock_user("platform_X", "user_C_px2", "pid_pa_C") # 关键：user_C_px2 也关联到 pid_pa_C

    # 生成 NPID
    npid_A = profile_manager_instance.generate_profile_document_id("pid_pa_A")
    npid_B = profile_manager_instance.generate_profile_document_id("pid_pa_B")
    npid_C = profile_manager_instance.generate_profile_document_id("pid_pa_C")

    logger.info(f"NPID_A: {npid_A}, NPID_B: {npid_B}, NPID_C: {npid_C}")

    # 1. 添加账户信息
    await profile_db_instance.ensure_profile_document_exists(npid_A, "pid_pa_A", "platform_X", "user_A_px1")
    await profile_db_instance.ensure_profile_document_exists(npid_A, "pid_pa_A", "platform_Y", "user_A_py1")
    # 重复添加，测试唯一性约束或逻辑
    await profile_db_instance.ensure_profile_document_exists(npid_A, "pid_pa_A", "platform_X", "user_A_px1") 

    await profile_db_instance.ensure_profile_document_exists(npid_B, "pid_pa_B", "platform_X", "user_B_px1")
    await profile_db_instance.ensure_profile_document_exists(npid_B, "pid_pa_B", "platform_Z", "user_B_pz1")

    await profile_db_instance.ensure_profile_document_exists(npid_C, "pid_pa_C", "platform_X", "user_C_px1")
    await profile_db_instance.ensure_profile_document_exists(npid_C, "pid_pa_C", "platform_Y", "user_C_py1")
    await profile_db_instance.ensure_profile_document_exists(npid_C, "pid_pa_C", "platform_X", "user_C_px2")


    # 2. 验证 platform_accounts
    logger.info("--- 验证用户A的账户信息 ---")
    doc_A = await profile_db_instance.get_profile_document(npid_A)
    if doc_A and "platform_accounts" in doc_A:
        expected_A = {"platform_X": ["user_A_px1"], "platform_Y": ["user_A_py1"]}
        # 对列表进行排序以确保比较的稳定性
        accounts_A = {k: sorted(v) for k, v in doc_A["platform_accounts"].items()}
        expected_A_sorted = {k: sorted(v) for k, v in expected_A.items()}
        if accounts_A == expected_A_sorted:
            logger.info(f"成功 (用户A): platform_accounts 匹配预期。得到: {accounts_A}")
        else:
            logger.error(f"失败 (用户A): platform_accounts 不匹配。预期: {expected_A_sorted}, 得到: {accounts_A}")
    else:
        logger.error(f"失败 (用户A): 未找到文档或 platform_accounts 字段。文档: {doc_A}")

    logger.info("--- 验证用户B的账户信息 ---")
    doc_B = await profile_db_instance.get_profile_document(npid_B)
    if doc_B and "platform_accounts" in doc_B:
        expected_B = {"platform_X": ["user_B_px1"], "platform_Z": ["user_B_pz1"]}
        accounts_B = {k: sorted(v) for k, v in doc_B["platform_accounts"].items()}
        expected_B_sorted = {k: sorted(v) for k, v in expected_B.items()}
        if accounts_B == expected_B_sorted:
            logger.info(f"成功 (用户B): platform_accounts 匹配预期。得到: {accounts_B}")
        else:
            logger.error(f"失败 (用户B): platform_accounts 不匹配。预期: {expected_B_sorted}, 得到: {accounts_B}")
    else:
        logger.error(f"失败 (用户B): 未找到文档或 platform_accounts 字段。文档: {doc_B}")

    logger.info("--- 验证用户C的账户信息 (同人多平台多账号) ---")
    doc_C = await profile_db_instance.get_profile_document(npid_C)
    if doc_C and "platform_accounts" in doc_C:
        expected_C = {"platform_X": ["user_C_px1", "user_C_px2"], "platform_Y": ["user_C_py1"]}
        accounts_C = {k: sorted(v) for k, v in doc_C["platform_accounts"].items()}
        expected_C_sorted = {k: sorted(v) for k, v in expected_C.items()}
        if accounts_C == expected_C_sorted:
            logger.info(f"成功 (用户C): platform_accounts 匹配预期。得到: {accounts_C}")
        else:
            logger.error(f"失败 (用户C): platform_accounts 不匹配。预期: {expected_C_sorted}, 得到: {accounts_C}")
    else:
        logger.error(f"失败 (用户C): 未找到文档或 platform_accounts 字段。文档: {doc_C}")

    # 3. 测试 get_profile_document 只请求 platform_accounts
    logger.info("--- 测试 get_profile_document fields=['platform_accounts'] (用户A) ---")
    doc_A_only_pa = await profile_db_instance.get_profile_document(npid_A, fields=["platform_accounts", "_id"]) # _id 会被自动加入
    if doc_A_only_pa and "platform_accounts" in doc_A_only_pa and "_id" in doc_A_only_pa:
        if len(doc_A_only_pa.keys()) == 2: # 应该只有 platform_accounts 和 _id
             logger.info(f"成功 (用户A fields): 只返回了 platform_accounts 和 _id。 Keys: {list(doc_A_only_pa.keys())}")
        else:
            logger.error(f"失败 (用户A fields): 返回了多余的字段。 Keys: {list(doc_A_only_pa.keys())}")
        
        expected_A = {"platform_X": ["user_A_px1"], "platform_Y": ["user_A_py1"]}
        accounts_A_pa_only = {k: sorted(v) for k, v in doc_A_only_pa["platform_accounts"].items()}
        expected_A_sorted_pa_only = {k: sorted(v) for k, v in expected_A.items()}
        if accounts_A_pa_only == expected_A_sorted_pa_only:
            logger.info(f"成功 (用户A fields): platform_accounts 内容匹配。")
        else:
            logger.error(f"失败 (用户A fields): platform_accounts 内容不匹配。预期: {expected_A_sorted_pa_only}, 得到: {accounts_A_pa_only}")
    else:
        logger.error(f"失败 (用户A fields): 使用 fields 参数获取 platform_accounts 失败。文档: {doc_A_only_pa}")


    logger.info("=" * 30 + " Platform Accounts 测试场景结束 " + "=" * 30)


async def run_all_tests():
    logger.info("开始所有测试...")
    
    db_file = global_config.profile.db_path
    if os.path.exists(db_file):
        logger.info(f"删除旧的数据库文件: {db_file}")
        os.remove(db_file)

    profile_db_instance = ProfileDB(db_path=db_file) 
    logger.info(f"ProfileDB (SQLite) 初始化完成，数据库文件: {profile_db_instance.db_path}")

    profile_manager_instance = ProfileManager(profile_db_instance=profile_db_instance) 
    logger.info("ProfileManager (SQLite) 初始化完成。")

    # 运行绰号测试
    await run_sobriquet_test_scenario(profile_db_instance, profile_manager_instance)

    # 运行 platform_accounts 测试
    await run_platform_accounts_test_scenario(profile_db_instance, profile_manager_instance)
    
    logger.info("所有测试场景结束。")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logging.getLogger("asyncio").setLevel(logging.INFO) # asyncio 日志级别调高，避免过多无关输出
    
    asyncio.run(run_all_tests())
