# profile/sobriquet/sobriquet_utils.py
import random
from typing import List, Dict, Tuple, Any, Optional

# 从 stubs 导入
from stubs.mock_config import global_config
from stubs.mock_dependencies import get_logger

logger = get_logger("sobriquet_utils")

def select_sobriquets_for_prompt(
    all_sobriquets_info_with_name_and_uid: Dict[str, Dict[str, Any]]
) -> List[Tuple[str, str, str, int]]:
    """
    从给定的绰号信息中，根据映射次数加权随机选择最多 N 个绰号用于 Prompt。
    此函数现在与 nickname_utils.py 中的 select_nicknames_for_prompt 逻辑更接近。

    Args:
        all_sobriquets_info_with_name_and_uid: 包含用户及其绰号和 UID 信息的字典，
            键是用户的真实名称或平台昵称 (person_name)，
            值是包含 "user_id" 和 "sobriquets" (群内常用绰号列表) 的字典。
            格式示例: { "张三": {"user_id": "uid1", "sobriquets": [{"绰号A": 次数}, {"绰号B": 次数}]}, ... }

    Returns:
        List[Tuple[str, str, str, int]]: 选中的绰号列表，每个元素为 (用户真实名称, user_id, 绰号, 次数)。
                                    按次数降序排序。
    """
    if not all_sobriquets_info_with_name_and_uid:
        return []

    candidates = []  # 存储 (用户真实名称, user_id, 绰号, 次数, 权重)
    smoothing_factor = global_config.profile.sobriquet_probability_smoothing # 沿用原配置名

    for user_actual_name, data in all_sobriquets_info_with_name_and_uid.items():
        user_id = data.get("user_id")
        # 在 WTF_Profile_system 中，绰号列表的键是 "sobriquets"
        # 而在 nickname_utils.py 中是 "nicknames"。这里我们统一为 "sobriquets"
        # 假设 ProfileManager 返回的数据结构中，绰号列表的键是 "sobriquets"
        sobriquets_list = data.get("sobriquets") 

        if not user_id or not isinstance(sobriquets_list, list):
            logger.warning(f"用户实际名称 '{user_actual_name}' 的数据格式无效或缺少 user_id/sobriquets。已跳过。 Data: {data}")
            continue
            
        for sobriquet_entry in sobriquets_list:
            if isinstance(sobriquet_entry, dict) and len(sobriquet_entry) == 1:
                # sobriquet_entry 格式为 {"绰号名": 次数}
                sobriquet_name, count = list(sobriquet_entry.items())[0]
                if isinstance(count, int) and count > 0 and isinstance(sobriquet_name, str) and sobriquet_name:
                    weight = count + smoothing_factor
                    candidates.append((user_actual_name, user_id, sobriquet_name, count, weight))
                else:
                    logger.warning(
                        f"用户实际名称 '{user_actual_name}' (UID: {user_id}) 的绰号条目无效: {sobriquet_entry}。已跳过。"
                    )
            else:
                logger.warning(f"用户实际名称 '{user_actual_name}' (UID: {user_id}) 的绰号条目格式无效: {sobriquet_entry}。已跳过。")

    if not candidates:
        return []

    max_sobriquets = global_config.profile.max_sobriquets_in_prompt # 沿用原配置名
    if max_sobriquets == 0:
        logger.warning("max_sobriquets_in_prompt 配置为0，不选择任何绰号。")
        return []
        
    num_to_select = min(max_sobriquets, len(candidates))

    try:
        selected_candidates_with_weight = weighted_sample_without_replacement(candidates, num_to_select)

        if len(selected_candidates_with_weight) < num_to_select:
            logger.debug(
                f"加权随机选择后数量不足 ({len(selected_candidates_with_weight)}/{num_to_select})，尝试补充选择次数最多的。"
            )
            # 确保 selected_ids 的元组结构与 candidates 中的一致，以便比较
            selected_ids_set = set(
                (c[0], c[1], c[2]) for c in selected_candidates_with_weight # (user_actual_name, user_id, sobriquet_name)
            )
            remaining_candidates = [c for c in candidates if (c[0], c[1], c[2]) not in selected_ids_set]
            remaining_candidates.sort(key=lambda x: x[3], reverse=True)  # 按原始次数 (index 3) 排序
            needed = num_to_select - len(selected_candidates_with_weight)
            selected_candidates_with_weight.extend(remaining_candidates[:needed])

    except Exception as e:
        logger.error(f"绰号加权随机选择时出错: {e}。将回退到选择次数最多的 Top N。", exc_info=True)
        candidates.sort(key=lambda x: x[3], reverse=True) 
        selected_candidates_with_weight = candidates[:num_to_select]

    # 格式化输出结果为 (用户真实名称, user_id, 绰号, 次数)，移除权重
    result = [(name, uid, sobriquet, count) for name, uid, sobriquet, count, _weight in selected_candidates_with_weight]
    result.sort(key=lambda x: x[3], reverse=True) 

    logger.debug(f"为 Prompt 选择的绰号 (含UID): {result}")
    return result


def format_sobriquet_prompt_injection(
    users_data_with_sobriquets: List[Dict[str, Any]], # 接收包含用户基本信息和已选绰号的数据
    # selected_group_sobriquets: Optional[List[Tuple[str, str, str, int]]] = None, # 这个参数现在合并到 users_data_with_sobriquets
    is_group_chat: bool = False
) -> str:
    """
    将用户基本信息（UID、平台昵称、群名片、群头衔）和可选的群内常用绰号信息格式化为注入 Prompt 的字符串。
    此函数合并了原 WTF_Profile_system 的 format_sobriquet_prompt_injection 和 nickname_utils.py 的 format_user_info_prompt 的部分功能。
    它现在期望 users_data_with_sobriquets 包含更全面的信息。

    Args:
        users_data_with_sobriquets: 用户信息列表，每个元素为字典，期望包含:
            "user_id": str, (必需)
            "platform_nickname": str, (必需，用户的平台昵称或真实名称)
            "group_cardname": Optional[str], (仅群聊相关)
            "group_titlename": Optional[str], (仅群聊相关)
            "selected_sobriquets": Optional[List[str]] (可选，此用户在本群被选中的常用绰号列表)
        is_group_chat: bool, 指示当前是否为群聊上下文。

    Returns:
        str: 格式化后的字符串，如果列表为空则返回空字符串。
    """
    if not users_data_with_sobriquets:
        return ""

    prompt_lines = ["以下是聊天记录中一些成员的信息，供你参考："]

    for user_data in users_data_with_sobriquets:
        user_id = user_data.get("user_id")
        platform_nickname = user_data.get("platform_nickname") # 这是用户在平台上的昵称或真实名称

        if not user_id or platform_nickname is None: 
            logger.warning(f"format_sobriquet_prompt_injection 跳过无效用户数据: {user_data}")
            continue

        line_parts = []
        if user_id == str(global_config.bot.qq_account): # 对机器人本身的特殊处理
            line_parts.append(f"uid:{user_id}，这是你，你的昵称为“{platform_nickname}”")
        else:
            line_parts.append(f"uid:{user_id}，用户平台昵称为“{platform_nickname}”")

        if is_group_chat:
            group_cardname = user_data.get("group_cardname")
            group_titlename = user_data.get("group_titlename")
            selected_sobriquets_for_user = user_data.get("selected_sobriquets") # 这是一个字符串列表

            if group_cardname:
                line_parts.append(f"本群群名片为“{group_cardname}”")
            
            if group_titlename:
                line_parts.append(f"群特殊头衔为“{group_titlename}”")

            if selected_sobriquets_for_user and isinstance(selected_sobriquets_for_user, list):
                sobriquets_str_parts = [f"“{s}”" for s in selected_sobriquets_for_user]
                if sobriquets_str_parts:
                    line_parts.append(f"ta 在本群常被称为：{'、'.join(sobriquets_str_parts)}")
        
        prompt_lines.append("，".join(line_parts))


    if len(prompt_lines) > 1:
        return "\n".join(prompt_lines) + "\n"
    else:
        return ""


def weighted_sample_without_replacement(
    candidates: List[Tuple[Any, ...]], # 候选列表，最后一个元素必须是权重 (float)
    k: int
) -> List[Tuple[Any, ...]]:
    """
    执行不重复的加权随机抽样。使用 A-ExpJ 算法思想的简化实现。
    期望 candidates 列表中的每个元组的最后一个元素是其权重。

    Args:
        candidates: 候选列表，例如 [(item1_data, ..., item1_weight), (item2_data, ..., item2_weight), ...]。
        k: 需要选择的数量。

    Returns:
        List[Tuple[Any, ...]]: 选中的元素列表（包含权重）。
    """
    if k <= 0:
        return []
    n = len(candidates)
    if k >= n:
        return candidates[:] 

    weighted_keys = [] 
    for i in range(n):
        weight = candidates[i][-1] # 假设权重是元组的最后一个元素
        if not isinstance(weight, (int, float)):
            logger.error(f"候选者 {candidates[i][:-1]} 的权重格式无效 ({weight})，已跳过。")
            continue

        if weight <= 0:
            log_key = float("-inf") 
            logger.warning(f"候选者 {candidates[i][:-1]} 的权重为非正数 ({weight})，抽中概率极低。")
        else:
            u = random.random() 
            if u == 0.0: u = 1e-9 
            log_key = -random.expovariate(1.0) / weight # key = -ln(U) / w_i
                                                      # 权重越大，key越小 (更接近0，因为-ln(U)是正数)
                                                      # 或者说，key 越负得少。排序时取最大的k个key。
        weighted_keys.append((log_key, i))

    # 对 log_key 进行排序。因为 log_key = -ln(U)/w，权重越大，log_key 越不负 (即越大)。
    # 所以我们应该选择具有最大 log_key 值的 k 个元素。
    weighted_keys.sort(key=lambda x: x[0], reverse=True) 
    
    selected_indices = [index for _log_key, index in weighted_keys[:k]]
    selected_items = [candidates[i] for i in selected_indices]

    return selected_items
