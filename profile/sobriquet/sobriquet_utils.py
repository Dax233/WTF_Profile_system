# profile/sobriquet/sobriquet_utils.py
# 此文件中的工具函数是纯Python逻辑，不直接与外部依赖交互。
# 它们依赖 global_config，我们将从 stubs 导入。
# 我们将从 stubs 导入 get_logger。

import random
from typing import List, Dict, Tuple, Any

# 从 stubs 导入
from stubs.mock_config import global_config
from stubs.mock_dependencies import get_logger


logger = get_logger("sobriquet_utils")


def select_sobriquets_for_prompt(
    all_sobriquets_info_with_uid: Dict[str, Dict[str, Any]]
) -> List[Tuple[str, str, str, int]]:
    """
    从给定的绰号信息中，根据映射次数加权随机选择最多 N 个绰号用于 Prompt。

    Args:
        all_sobriquets_info_with_uid: 包含用户及其绰号和 UID 信息的字典，格式为
                        { "用户名1": {"user_id": "uid1", "sobriquets": [{"绰号A": 次数}, {"绰号B": 次数}]}, ... }
                        注意：这里的键是 person_name。

    Returns:
        List[Tuple[str, str, str, int]]: 选中的绰号列表，每个元素为 (用户名, user_id, 绰号, 次数)。
                                    按次数降序排序。
    """
    if not all_sobriquets_info_with_uid:
        return []

    candidates = []  # 存储 (用户名, user_id, 绰号, 次数, 权重)
    smoothing_factor = global_config.profile.sobriquet_probability_smoothing

    for user_name, data in all_sobriquets_info_with_uid.items():
        user_id = data.get("user_id")
        sobriquets_list = data.get("sobriquets")

        if not user_id or not isinstance(sobriquets_list, list):
            logger.warning(f"用户 '{user_name}' 的数据格式无效或缺少 user_id/sobriquets。已跳过。 Data: {data}")
            continue
            
        for sobriquet_entry in sobriquets_list:
            if isinstance(sobriquet_entry, dict) and len(sobriquet_entry) == 1:
                sobriquet, count = list(sobriquet_entry.items())[0]
                if isinstance(count, int) and count > 0 and isinstance(sobriquet, str) and sobriquet:
                    weight = count + smoothing_factor
                    candidates.append((user_name, user_id, sobriquet, count, weight))
                else:
                    logger.warning(
                        f"用户 '{user_name}' (UID: {user_id}) 的绰号条目无效: {sobriquet_entry} (次数非正整数或绰号为空)。已跳过。"
                    )
            else:
                logger.warning(f"用户 '{user_name}' (UID: {user_id}) 的绰号条目格式无效: {sobriquet_entry}。已跳过。")

    if not candidates:
        return []

    max_sobriquets = global_config.profile.max_sobriquets_in_prompt
    num_to_select = min(max_sobriquets, len(candidates))

    try:
        # weighted_sample_without_replacement 期望的元组是 (用户名, user_id, 绰号, 次数, 权重)
        selected_candidates_with_weight = weighted_sample_without_replacement(candidates, num_to_select)

        if len(selected_candidates_with_weight) < num_to_select:
            logger.debug(
                f"加权随机选择后数量不足 ({len(selected_candidates_with_weight)}/{num_to_select})，尝试补充选择次数最多的。"
            )
            selected_ids = set(
                (c[0], c[1], c[2]) for c in selected_candidates_with_weight # (user_name, user_id, sobriquet)
            )
            remaining_candidates = [c for c in candidates if (c[0], c[1], c[2]) not in selected_ids]
            remaining_candidates.sort(key=lambda x: x[3], reverse=True)  # 按原始次数 (index 3) 排序
            needed = num_to_select - len(selected_candidates_with_weight)
            selected_candidates_with_weight.extend(remaining_candidates[:needed])

    except Exception as e:
        logger.error(f"绰号加权随机选择时出错: {e}。将回退到选择次数最多的 Top N。", exc_info=True)
        candidates.sort(key=lambda x: x[3], reverse=True) # 按原始次数 (index 3) 排序
        selected_candidates_with_weight = candidates[:num_to_select]

    # 格式化输出结果为 (用户名, user_id, 绰号, 次数)，移除权重
    result = [(user, uid, nick, count) for user, uid, nick, count, _weight in selected_candidates_with_weight]
    result.sort(key=lambda x: x[3], reverse=True) # 按次数 (index 3) 降序排序

    logger.debug(f"为 Prompt 选择的绰号 (含UID): {result}")
    return result


def format_sobriquet_prompt_injection(selected_sobriquets_with_uid: List[Tuple[str, str, str, int]]) -> str:
    """
    将选中的绰号信息（含UID）格式化为注入 Prompt 的字符串。

    Args:
        selected_sobriquets_with_uid: 选中的绰号列表 (用户名, user_id, 绰号, 次数)。

    Returns:
        str: 格式化后的字符串，如果列表为空则返回空字符串。
    """
    if not selected_sobriquets_with_uid:
        return ""

    prompt_lines = ["以下是聊天记录中一些成员在本群的绰号信息（按常用度排序）与 uid 信息，供你参考："]
    grouped_by_user: Dict[Tuple[str, str], List[str]] = {} # 键: (user_name, user_id)

    for user_name, user_id, sobriquet, _count in selected_sobriquets_with_uid:
        user_key = (user_name, user_id)
        if user_key not in grouped_by_user:
            grouped_by_user[user_key] = []
        grouped_by_user[user_key].append(f"“{sobriquet}”")

    for (user_name, user_id), sobriquets_list_str_parts in grouped_by_user.items():
        sobriquets_str = "、".join(sobriquets_list_str_parts)
        prompt_lines.append(f"- {user_name}({user_id})，ta 可能被称为：{sobriquets_str}")

    if len(prompt_lines) > 1:
        return "\n".join(prompt_lines) + "\n"
    else:
        return ""


def weighted_sample_without_replacement(
    candidates: List[Tuple[str, str, str, int, float]], k: int
) -> List[Tuple[str, str, str, int, float]]:
    """
    执行不重复的加权随机抽样。使用 A-ExpJ 算法思想的简化实现。

    Args:
        candidates: 候选列表，每个元素为 (用户名, user_id, 绰号, 次数, 权重)。
        k: 需要选择的数量。

    Returns:
        List[Tuple[str, str, str, int, float]]: 选中的元素列表（包含权重）。
    """
    if k <= 0:
        return []
    n = len(candidates)
    if k >= n:
        return candidates[:] # 返回副本

    weighted_keys = [] # (log_key, original_index)
    for i in range(n):
        # candidates[i] is (用户名, user_id, 绰号, 次数, 权重)
        # 权重是第5个元素，索引为4
        weight = candidates[i][4] 
        if weight <= 0:
            log_key = float("-inf") # 保证不会被选中 (除非所有权重都 <=0)
            logger.warning(f"候选者 {candidates[i][:3]} 的权重为非正数 ({weight})，抽中概率极低。")
        else:
            # 生成一个随机数 u，通常是 (0, 1] 或 [0, 1)
            # random.random() -> [0.0, 1.0)
            # 为了避免 log(0)，使用 random.uniform(epsilon, 1.0) 或者确保 u 不为0
            u = random.random() 
            if u == 0.0: u = 1e-9 # 避免 log(0)
            log_key = (1/weight) * (random.expovariate(1.0)) # A-ExpJ 使用 r^(1/w), 这里用 exp(-key/w) 的思想，key = -ln(u)
                                                            # 或者更简单： key_i = U_i^(1/w_i) where U_i ~ U(0,1)
                                                            # 或者使用 exp(ln(U_i)/w_i)
                                                            # 这里使用 Knuth/Yao 的方法: key_i = -ln(U_i) / w_i
            log_u = -random.expovariate(1.0) # 等价于 -math.log(random.random())
            log_key = log_u / weight


        weighted_keys.append((log_key, i))

    # 对 log_key 进行排序 (A-ExpJ 是取最小的 k 个，这里因为是负对数或者除了权重，所以排序方向可能不同)
    # 如果 key_i = -ln(U_i) / w_i, 我们要取最小的 k 个
    # 如果 key_i = U_i^(1/w_i), 我们要取最大的 k 个
    # 当前实现是 log_key = -ln(U_i) / w_i (因为 expovariate(1.0) 产生的是指数分布的值，相当于 -ln(U))
    # 所以我们应该选择具有最小 log_key 值的 k 个元素。
    # 原代码是 reverse=True，这意味着它取最大的 k 个。这对应于 U_i^(1/w_i) 类型的键。
    # 为了与之前的逻辑（可能存在的细微差别）保持一致，我们暂时保留 reverse=True，
    # 但标准的 A-ExpJ (using -ln(U)/w) 是取最小的。
    # 如果权重越大，被选中的概率应该越大。
    # -ln(U) 是一个正数，除以一个大的 w 会使 key 变小。所以应该取最小的。
    # 如果是 U^(1/w)，U在(0,1)，1/w 也是正数。w 越大，1/w 越小。U^(小正数) 更接近1。所以取最大的。
    # 我们将坚持使用 `log_key = -random.expovariate(1.0) / weight` 并取最小的 k 个。
    # 因此，`reverse=False`。

    weighted_keys.sort(key=lambda x: x[0], reverse=False) # 改为 False，取最小的 k 个 log_key
    
    selected_indices = [index for _log_key, index in weighted_keys[:k]]
    selected_items = [candidates[i] for i in selected_indices]

    return selected_items
