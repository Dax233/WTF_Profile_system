# profile/sobriquet/sobriquet_db.py
import sqlite3
import json
import threading # 用于支持多线程访问 SQLite 的锁
from typing import Optional, List, Dict, Any

# 从 stubs 导入
from stubs.mock_dependencies import get_logger

logger = get_logger("SobriquetDB_SQLite")

class SobriquetDB:
    """
    处理与用户画像（profile_info）相关的数据库操作 (SQLite)。
    负责绰号数据的写入和 profile_info 表基本结构的维护。
    文档的 _id 是基于 person_info_pid 生成的 profile_document_id (NaturalPersonID)。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock() # SQLite 在多线程写入时需要锁
        self._create_tables_if_not_exists()
        logger.info(f"SobriquetDB_SQLite 初始化成功，使用数据库文件: '{db_path}'")

    def _get_connection(self):
        # 为每个操作或短期使用创建连接，或使用线程安全的连接池
        # 对于简单的测试，每次创建一个新连接是可以的
        return sqlite3.connect(self.db_path)

    def _create_tables_if_not_exists(self):
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # profile_info 表结构基于 profile.md
                # NaturalPersonID 作为主键 (_id)
                # platform_accounts 和 sobriquets_by_group 将存储为 JSON 字符串
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_info (
                    _id TEXT PRIMARY KEY,
                    person_info_pid_ref TEXT,
                    platform_accounts TEXT,
                    sobriquets_by_group TEXT,
                    identity TEXT,
                    personality TEXT,
                    impression TEXT,
                    relationship_metrics TEXT,
                    creation_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_updated_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """)
                conn.commit()
                logger.info("表 'profile_info' 已检查/创建。")
            except sqlite3.Error as e:
                logger.error(f"创建表 'profile_info' 时发生 SQLite 错误: {e}", exc_info=True)
                raise
            finally:
                conn.close()

    def is_available(self) -> bool:
        # 对于 SQLite，如果文件路径存在且可写，通常认为是可用的
        # 这里简单返回 True，因为构造时会尝试创建
        return True

    def ensure_profile_document_exists(self,
                                     profile_document_id: str,
                                     person_info_pid_ref: str,
                                     platform: str,
                                     platform_user_id: str):
        """
        确保 profile_info 中存在指定 profile_document_id 的文档。
        如果文档不存在，则使用提供的用户信息创建它，并记录 person_info_pid_ref 及平台账户信息。
        """
        if not profile_document_id:
            logger.error("profile_document_id 为空，无法确保文档存在。")
            return

        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # 步骤 1: 尝试插入新文档，如果已存在则忽略
                cursor.execute("""
                INSERT OR IGNORE INTO profile_info (_id, person_info_pid_ref, platform_accounts, sobriquets_by_group)
                VALUES (?, ?, ?, ?)
                """, (profile_document_id, person_info_pid_ref, json.dumps({}), json.dumps({})))
                
                if cursor.rowcount > 0:
                    logger.debug(f"为 profile_document_id '{profile_document_id}' (pid_ref: {person_info_pid_ref}) 创建了新的 profile_info 记录。")

                # 步骤 2: 更新 platform_accounts
                # 首先读取现有的 platform_accounts
                cursor.execute("SELECT platform_accounts FROM profile_info WHERE _id = ?", (profile_document_id,))
                row = cursor.fetchone()
                
                current_platform_accounts = {}
                if row and row[0]:
                    try:
                        current_platform_accounts = json.loads(row[0])
                    except json.JSONDecodeError:
                        logger.error(f"解析 profile_id '{profile_document_id}' 的 platform_accounts JSON 失败: {row[0]}")
                        # 如果解析失败，可以选择是否覆盖为一个新的空字典
                        current_platform_accounts = {}


                if platform not in current_platform_accounts:
                    current_platform_accounts[platform] = []
                
                #确保 platform_user_id 不重复添加
                if str(platform_user_id) not in current_platform_accounts[platform]:
                    current_platform_accounts[platform].append(str(platform_user_id))
                
                # 写回更新后的 platform_accounts 和 last_updated_timestamp
                cursor.execute("""
                UPDATE profile_info
                SET platform_accounts = ?, last_updated_timestamp = CURRENT_TIMESTAMP
                WHERE _id = ?
                """, (json.dumps(current_platform_accounts), profile_document_id))
                
                conn.commit()
                logger.debug(f"为 profile_document_id '{profile_document_id}' 的平台 '{platform}' 添加/更新了 platform_user_id '{platform_user_id}'。")

            except sqlite3.Error as e:
                logger.error(f"确保 profile_info 文档存在时发生 SQLite 错误 (profile_id '{profile_document_id}'): {e}", exc_info=True)
                conn.rollback() # 发生错误时回滚
                raise
            finally:
                conn.close()

    def update_group_sobriquet_count(self, profile_document_id: str, platform: str, group_id_str: str, sobriquet_name: str):
        """
        更新 profile_document_id 对应文档中特定群组的特定绰号的计数。
        """
        if not profile_document_id:
            logger.error("profile_document_id 为空，无法更新绰号计数。")
            return

        group_key = f"{platform}-{group_id_str}"

        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # 1. 读取当前的 sobriquets_by_group
                cursor.execute("SELECT sobriquets_by_group FROM profile_info WHERE _id = ?", (profile_document_id,))
                row = cursor.fetchone()

                if not row:
                    logger.error(f"更新绰号计数失败：未找到 profile_document_id '{profile_document_id}' 的记录。")
                    return

                sobriquets_by_group_data = {}
                if row[0]:
                    try:
                        sobriquets_by_group_data = json.loads(row[0])
                    except json.JSONDecodeError:
                        logger.error(f"解析 profile_id '{profile_document_id}' 的 sobriquets_by_group JSON 失败: {row[0]}")
                        # 如果解析失败，可以选择是否覆盖为一个新的空字典
                        sobriquets_by_group_data = {}


                # 2. 更新数据
                if group_key not in sobriquets_by_group_data:
                    sobriquets_by_group_data[group_key] = {"sobriquets": []}
                
                group_sobriquets_list = sobriquets_by_group_data[group_key].get("sobriquets", [])
                
                found_sobriquet = False
                for item in group_sobriquets_list:
                    if isinstance(item, dict) and item.get("name") == sobriquet_name:
                        item["count"] = item.get("count", 0) + 1
                        found_sobriquet = True
                        break
                
                if not found_sobriquet:
                    group_sobriquets_list.append({"name": sobriquet_name, "count": 1})
                
                sobriquets_by_group_data[group_key]["sobriquets"] = group_sobriquets_list

                # 3. 写回更新后的 sobriquets_by_group 和 last_updated_timestamp
                cursor.execute("""
                UPDATE profile_info
                SET sobriquets_by_group = ?, last_updated_timestamp = CURRENT_TIMESTAMP
                WHERE _id = ?
                """, (json.dumps(sobriquets_by_group_data), profile_document_id))
                
                conn.commit()
                logger.debug(f"为 profile_id '{profile_document_id}' 在群组 '{group_key}' 更新/添加绰号 '{sobriquet_name}'。")

            except sqlite3.Error as e:
                logger.error(f"更新群组绰号计数时发生 SQLite 错误 (profile_id '{profile_document_id}', group '{group_key}', sobriquet '{sobriquet_name}'): {e}", exc_info=True)
                conn.rollback()
                raise
            finally:
                conn.close()

    async def get_profile_document_by_id_for_find(self, profile_doc_id: str, projection: Optional[Dict] = None) -> Optional[Dict]:
        """
        根据 profile_doc_id 获取文档，并根据 projection 模拟 MongoDB 的字段选择。
        这是为 MockDbCollection.find 提供的辅助方法。
        """
        # logger.debug(f"SQLite get_profile_document_by_id_for_find called for {profile_doc_id} with projection {projection}")
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # 简单实现：总是获取 sobriquets_by_group 和 _id (person_info_pid_ref 也可能有用)
                # 实际的 projection 解析会更复杂
                cursor.execute("SELECT _id, sobriquets_by_group, person_info_pid_ref FROM profile_info WHERE _id = ?", (profile_doc_id,))
                row = cursor.fetchone()

                if not row:
                    return None

                doc_id_from_db, sobriquets_json, pid_ref_from_db = row
                
                # 构建返回的文档结构，模拟 projection
                # "_id": 1, f"sobriquets_by_group.{group_key_in_db}.sobriquets": 1
                
                result_doc = {}
                if projection:
                    if projection.get("_id"):
                        result_doc["_id"] = doc_id_from_db
                    # if projection.get("person_info_pid_ref"): # 可选
                    #     result_doc["person_info_pid_ref"] = pid_ref_from_db

                    # 处理 sobriquets_by_group 的投影
                    # 这是一个简化版本，它不完全复制 MongoDB 的点表示法路径投影到嵌套数组元素
                    # 它将返回整个 sobriquets_by_group 字段，然后由调用者 (ProfileManager) 处理
                    # 或者，我们需要在这里解析 group_key_in_db from projection string
                    # e.g. "sobriquets_by_group.qq-group123.sobriquets": 1
                    # For now, let's return the whole sobriquets_by_group if any part of it is requested.
                    
                    # 检查 projection 中是否请求了 sobriquets_by_group 的任何部分
                    requested_sobriquets = False
                    group_key_from_projection = None

                    for key_proj in projection.keys():
                        if key_proj.startswith("sobriquets_by_group"):
                            requested_sobriquets = True
                            # 尝试提取 group_key, e.g., "qq-group123" from "sobriquets_by_group.qq-group123.sobriquets"
                            parts = key_proj.split('.')
                            if len(parts) > 1 and parts[0] == "sobriquets_by_group":
                                group_key_from_projection = parts[1]
                            break
                    
                    if requested_sobriquets:
                        try:
                            sobriquets_data = json.loads(sobriquets_json) if sobriquets_json else {}
                            if group_key_from_projection and group_key_from_projection in sobriquets_data:
                                # 如果指定了特定的 group_key，则只返回该 group 的数据
                                result_doc["sobriquets_by_group"] = {
                                    group_key_from_projection: sobriquets_data[group_key_from_projection]
                                }
                            elif not group_key_from_projection: # 如果没有指定 group_key (例如 projection 是 "sobriquets_by_group":1)
                                result_doc["sobriquets_by_group"] = sobriquets_data
                            else: # 指定的 group_key 不存在
                                result_doc["sobriquets_by_group"] = {}

                        except json.JSONDecodeError:
                            logger.error(f"解析 profile_id '{profile_doc_id}' 的 sobriquets_by_group JSON 失败 (in find): {sobriquets_json}")
                            result_doc["sobriquets_by_group"] = {}
                else: # 无投影，返回基本结构
                    result_doc["_id"] = doc_id_from_db
                    result_doc["person_info_pid_ref"] = pid_ref_from_db
                    try:
                        result_doc["sobriquets_by_group"] = json.loads(sobriquets_json) if sobriquets_json else {}
                    except json.JSONDecodeError:
                         result_doc["sobriquets_by_group"] = {}
                
                # logger.debug(f"SQLite find result for {profile_doc_id}: {result_doc}")
                return result_doc

            except sqlite3.Error as e:
                logger.error(f"获取 profile_info 文档时发生 SQLite 错误 (profile_id '{profile_doc_id}'): {e}", exc_info=True)
                return None
            finally:
                conn.close()

