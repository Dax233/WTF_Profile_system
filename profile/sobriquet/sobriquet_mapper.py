# profile/sobriquet/sobriquet_mapper.py
from typing import Dict, Optional
# 从 stubs 导入
from stubs.mock_dependencies import get_logger

logger = get_logger("sobriquet_mapper")


def build_mapping_prompt(chat_history_str: str, bot_reply: str, user_name_map: Optional[Dict[str, str]] = None) -> str:
    """
    构建用于 LLM 进行绰号映射分析的 Prompt。
    与 nickname_mapper.py 中的 prompt 逻辑同步，但使用 "sobriquet" 术语。

    Args:
        chat_history_str: 格式化后的聊天历史记录字符串。
        bot_reply: Bot 的最新回复字符串。
        user_name_map: 可选的，用户 ID 到已知名称（person_name 或 fallback sobriquet）的映射。
                       如果提供，会包含在 prompt 中作为已知用户信息。

    Returns:
        str: 构建好的 Prompt 字符串。
    """
    user_list_str_section = ""
    if user_name_map and isinstance(user_name_map, dict):
        user_list_items = [f"- {uid}: {name}" for uid, name in user_name_map.items() if uid and name]
        if user_list_items:
            user_list_str = "\n".join(user_list_items)
            user_list_str_section = f"""
已知用户信息（ID: 名称）：
{user_list_str}
"""
    else:
        user_list_str_section = """
已知用户信息（ID: 名称）：
无明确提供，请基于聊天记录上下文判断。
"""

    # 核心 Prompt 内容，与 nickname_mapper.py 的逻辑一致，但术语替换为 sobriquet
    prompt = f"""
任务：仔细分析以下聊天记录和“你的最新回复”，判断其中是否明确提到了某个用户的绰号 (sobriquet)，并且这个绰号可以清晰地与一个特定的用户 ID 对应起来。
{user_list_str_section}
聊天记录：
---
{chat_history_str}
---

你的最新回复：
{bot_reply}

分析要求与输出格式：
1.  找出聊天记录和“你的最新回复”中可能是用户绰号 (sobriquet) 的词语。
2.  判断这些绰号是否在上下文中**清晰、无歧义**地指向了“已知用户信息”列表中的**某一个特定用户 ID** (如果提供了已知用户信息) 或聊天记录中能明确识别出的用户ID。必须是强关联，避免猜测。
3.  **不要**输出你自己（名称后带"(你)"的用户）的绰号映射。
    **不要**输出与用户已知名称完全相同的词语作为绰号。
    **不要**将在“你的最新回复”中你对他人使用的称呼或绰号进行映射（只分析聊天记录中他人对用户的称呼）。
    **不要**输出指代不明或过于通用的词语（如“大佬”、“兄弟”、“那个谁”等，除非上下文能非常明确地指向特定用户）。
4.  如果找到了**至少一个**满足上述所有条件的**明确**的用户 ID 到绰号的映射关系，请输出 JSON 对象：
        ```json
        {{
            "is_exist": true,
            "data": {{
                "用户A的数字ID": "绰号_A",
                "用户B的数字ID": "绰号_B"
            }}
        }}
        ```
        - `"data"` 字段的键必须是用户的**数字 ID (字符串形式)**，值是对应的**绰号 (字符串形式)**。
        - 只包含你能**百分百确认**映射关系的条目。宁缺毋滥。
    如果**无法找到任何一个**满足条件的明确映射关系，请输出 JSON 对象：
        ```json
        {{
            "is_exist": false
        }}
        ```
5.  请**仅**输出 JSON 对象，不要包含任何额外的解释、注释或代码块标记之外的文本。

输出：
"""
    # logger.debug(f"构建的绰号映射 Prompt (部分):\n{prompt[:500]}...") # 日志记录移到调用处 (SobriquetManager)
    return prompt
