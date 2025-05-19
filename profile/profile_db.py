# profile/profile_db.py
import sqlite3
import json
import threading
import asyncio # 新增
from typing import Optional, List, Dict, Any
import datetime

# 从 stubs 导入
from stubs.mock_dependencies import get_logger 

logger = get_logger("ProfileDB_SQLite")

class ProfileDB:
    """
    处理与用户画像（profile_info）相关的数据库操作 (SQLite)。
    负责 profile_info 表的创建、读写等。
    文档的 _id 是 NaturalPersonID。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock() 
        self._create_tables_if_not_exists() # 同步创建表
        logger.info(f"ProfileDB_SQLite 初始化成功，使用数据库文件: '{db_path}'")

    def _get_connection_sync(self): # 同步获取连接的方法
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row 
        return conn

    def _create_tables_if_not_exists(self): # 同步方法
        with self._lock:
            conn = self._get_connection_sync()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_info (
                    _id TEXT PRIMARY KEY,
                    person_info_pid_ref TEXT,
                    platform_accounts TEXT,
                    identity TEXT,
                    personality TEXT,
                    sobriquets_by_group TEXT,
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
        return True

    async def ensure_profile_document_exists(self,
                                     profile_document_id: str,
                                     person_info_pid_ref: Optional[str] = None,
                                     platform: Optional[str] = None,
                                     platform_user_id: Optional[str] = None) -> bool:
        if not profile_document_id:
            logger.error("profile_document_id 为空，无法确保文档存在。")
            return False

        def _sync_ensure_doc():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    cursor = conn.cursor()
                    cursor.execute("""
                    INSERT OR IGNORE INTO profile_info (
                        _id, person_info_pid_ref, 
                        platform_accounts, identity, personality, 
                        sobriquets_by_group, impression, relationship_metrics,
                        last_updated_timestamp 
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
                        profile_document_id, person_info_pid_ref,
                        json.dumps({}), json.dumps({}), json.dumps({}),
                        json.dumps({}), json.dumps([]), json.dumps({}),
                    ))
                    
                    created_new = cursor.rowcount > 0
                    if created_new:
                        logger.debug(f"为 profile_document_id '{profile_document_id}' (pid_ref: {person_info_pid_ref}) 创建了新的 profile_info 记录。")
                    
                    if platform and platform_user_id:
                        cursor.execute("SELECT platform_accounts FROM profile_info WHERE _id = ?", (profile_document_id,))
                        row = cursor.fetchone()
                        current_platform_accounts = {}
                        if row and row["platform_accounts"]:
                            try:
                                current_platform_accounts = json.loads(row["platform_accounts"])
                            except json.JSONDecodeError:
                                logger.error(f"解析 profile_id '{profile_document_id}' 的 platform_accounts JSON 失败: {row['platform_accounts']}")
                                current_platform_accounts = {}

                        if platform not in current_platform_accounts:
                            current_platform_accounts[platform] = []
                        
                        if str(platform_user_id) not in current_platform_accounts[platform]:
                            current_platform_accounts[platform].append(str(platform_user_id))
                            cursor.execute("""
                            UPDATE profile_info
                            SET platform_accounts = ?, last_updated_timestamp = CURRENT_TIMESTAMP
                            WHERE _id = ?
                            """, (json.dumps(current_platform_accounts), profile_document_id))
                            logger.debug(f"为 profile_document_id '{profile_document_id}' 的平台 '{platform}' 添加了 platform_user_id '{platform_user_id}'。")
                    
                    conn.commit()
                    return True
                except sqlite3.Error as e:
                    logger.error(f"确保 profile_info 文档存在时发生 SQLite 错误 (profile_id '{profile_document_id}'): {e}", exc_info=True)
                    conn.rollback()
                    return False
                finally:
                    conn.close()
        return await asyncio.to_thread(_sync_ensure_doc)


    async def get_profile_document(self, profile_document_id: str, fields: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        if not profile_document_id:
            return None

        def _sync_get_doc():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    cursor = conn.cursor()
                    query_fields = "*"
                    effective_fields_to_select = []

                    if fields:
                        effective_fields_to_select = list(fields) # 复制列表
                        if "_id" not in effective_fields_to_select:
                            effective_fields_to_select.insert(0, "_id") # 确保 _id 总是在前面
                        query_fields = ", ".join(effective_fields_to_select)
                    else: # 获取所有字段定义
                        cursor.execute(f"PRAGMA table_info(profile_info);")
                        effective_fields_to_select = [row['name'] for row in cursor.fetchall()]
                        query_fields = "*"


                    cursor.execute(f"SELECT {query_fields} FROM profile_info WHERE _id = ?", (profile_document_id,))
                    row = cursor.fetchone()

                    if not row:
                        return None

                    doc = dict(row)
                    json_field_names = ["platform_accounts", "identity", "personality", "sobriquets_by_group", "impression", "relationship_metrics"]
                    for field_name in json_field_names:
                        if field_name in doc and doc[field_name] is not None:
                            try:
                                doc[field_name] = json.loads(doc[field_name])
                            except json.JSONDecodeError:
                                logger.error(f"获取文档时解析字段 '{field_name}' JSON 失败 for id '{profile_document_id}'. 内容: {doc[field_name]}")
                                doc[field_name] = {} if field_name != "impression" else []
                    
                    if fields: # 如果最初指定了字段，只返回那些（包括我们可能添加的_id）
                        return {k: doc[k] for k in effective_fields_to_select if k in doc}
                    return doc
                except sqlite3.Error as e:
                    logger.error(f"获取 profile_info 文档时 SQLite 错误 (id '{profile_document_id}'): {e}", exc_info=True)
                    return None
                finally:
                    conn.close()
        return await asyncio.to_thread(_sync_get_doc)

    async def update_profile_fields(self, profile_document_id: str, updates: Dict[str, Any]) -> bool:
        if not profile_document_id or not updates:
            return False

        def _sync_update_fields():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    cursor = conn.cursor()
                    set_clauses = []
                    values = []
                    valid_fields = ["person_info_pid_ref", "platform_accounts", "identity", "personality", 
                                    "sobriquets_by_group", "impression", "relationship_metrics"]

                    for field_name, field_value in updates.items():
                        if field_name not in valid_fields:
                            logger.warning(f"尝试更新无效字段 '{field_name}' for id '{profile_document_id}'。已跳过。")
                            continue
                        
                        set_clauses.append(f"{field_name} = ?")
                        if isinstance(field_value, (dict, list)):
                            values.append(json.dumps(field_value))
                        else:
                            values.append(field_value)
                    
                    if not set_clauses:
                        logger.info(f"没有有效的字段需要更新 for id '{profile_document_id}'。")
                        return True

                    set_clauses.append("last_updated_timestamp = CURRENT_TIMESTAMP")
                    sql = f"UPDATE profile_info SET {', '.join(set_clauses)} WHERE _id = ?"
                    values.append(profile_document_id)
                    
                    cursor.execute(sql, tuple(values))
                    conn.commit()
                    
                    if cursor.rowcount == 0:
                        logger.warning(f"更新 profile_fields 失败：未找到 profile_document_id '{profile_document_id}' 或数据未改变。")
                        return False
                    return True
                except sqlite3.Error as e:
                    logger.error(f"更新 profile_fields 时 SQLite 错误 (id '{profile_document_id}'): {e}", exc_info=True)
                    conn.rollback()
                    return False
                finally:
                    conn.close()
        return await asyncio.to_thread(_sync_update_fields)

    async def update_profile_field(self, profile_document_id: str, field_name: str, field_value: Any) -> bool:
        return await self.update_profile_fields(profile_document_id, {field_name: field_value})
    
    async def get_profile_field(self, profile_document_id: str, field_name: str) -> Optional[Any]:
        doc = await self.get_profile_document(profile_document_id, fields=[field_name])
        return doc.get(field_name) if doc else None

    async def update_group_sobriquet_count(self, profile_document_id: str, platform: str, group_id_str: str, sobriquet_name: str) -> bool:
        if not profile_document_id:
            logger.error("profile_document_id 为空，无法更新绰号计数。")
            return False

        group_key = f"{platform}-{group_id_str}"

        def _sync_update_sobriquet():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    cursor = conn.cursor()
                    # 先确保文档存在，如果不存在，ensure_profile_document_exists 会创建它
                    # 注意：ensure_profile_document_exists 是异步的，这里需要同步调用或调整
                    # 为了简化，这里假设调用此方法前文档已存在，或在 ensure_profile_document_exists 之后调用
                    # 或者，我们可以在这里再次调用 ensure_profile_document_exists 的同步版本（如果提供）
                    # 更好的做法是在上层逻辑中确保文档已存在

                    cursor.execute("SELECT sobriquets_by_group FROM profile_info WHERE _id = ?", (profile_document_id,))
                    row = cursor.fetchone()

                    if not row:
                        logger.error(f"更新绰号计数失败：未找到 profile_document_id '{profile_document_id}' 的记录。")
                        # 尝试创建它，如果它真的不存在 (这可能表明上层逻辑有缺陷)
                        # 我们不能直接在这里调用异步的 ensure_profile_document_exists
                        # 所以，这里我们假设它应该存在。如果不存在，就返回 False。
                        # 实际上，SobriquetManager 在调用此方法前会调用 ensure_profile_document_exists
                        return False 

                    sobriquets_by_group_data = {}
                    if row["sobriquets_by_group"]:
                        try:
                            sobriquets_by_group_data = json.loads(row["sobriquets_by_group"])
                        except json.JSONDecodeError:
                            logger.error(f"解析 profile_id '{profile_document_id}' 的 sobriquets_by_group JSON 失败: {row['sobriquets_by_group']}")
                            sobriquets_by_group_data = {}
                    
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

                    cursor.execute("""
                    UPDATE profile_info
                    SET sobriquets_by_group = ?, last_updated_timestamp = CURRENT_TIMESTAMP
                    WHERE _id = ?
                    """, (json.dumps(sobriquets_by_group_data), profile_document_id))
                    
                    conn.commit()
                    logger.debug(f"为 profile_id '{profile_document_id}' 在群组 '{group_key}' 更新/添加绰号 '{sobriquet_name}'。")
                    return True

                except sqlite3.Error as e:
                    logger.error(f"更新群组绰号计数时发生 SQLite 错误 (profile_id '{profile_document_id}', group '{group_key}', sobriquet '{sobriquet_name}'): {e}", exc_info=True)
                    conn.rollback()
                    return False
                finally:
                    conn.close()
        return await asyncio.to_thread(_sync_update_sobriquet)

    async def get_profile_document_for_find_projection(self, profile_doc_id: str, projection: Optional[Dict] = None) -> Optional[Dict]:
        # 这个方法主要是为了适配之前 ProfileManager 的 find 逻辑。
        # 现在 ProfileManager 可以直接使用 get_profile_document。
        # 但如果仍需模拟 MongoDB 的投影方式，可以保留或调整。
        
        def _sync_get_doc_for_projection():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    # 确定需要查询的字段
                    fields_to_fetch_set = set()
                    if projection:
                        for key, value in projection.items():
                            if value == 1: # MongoDB-style projection
                                if key == "_id" or not key.startswith("sobriquets_by_group."):
                                    fields_to_fetch_set.add(key.split('.')[0]) # 获取顶级字段
                                elif key.startswith("sobriquets_by_group."):
                                     fields_to_fetch_set.add("sobriquets_by_group")


                    if not fields_to_fetch_set: # 如果没有指定投影，或投影为空，则获取所有
                        query_fields_str = "*"
                        cursor = conn.cursor()
                        cursor.execute(f"PRAGMA table_info(profile_info);") # 获取所有列名
                        all_db_fields = [row['name'] for row in cursor.fetchall()]
                    else:
                        if "_id" not in fields_to_fetch_set : # 确保 _id 总是被选择
                            fields_to_fetch_set.add("_id")
                        query_fields_str = ", ".join(list(fields_to_fetch_set))
                        all_db_fields = list(fields_to_fetch_set)


                    cursor = conn.cursor()
                    cursor.execute(f"SELECT {query_fields_str} FROM profile_info WHERE _id = ?", (profile_doc_id,))
                    row = cursor.fetchone()

                    if not row:
                        return None

                    doc = {field: row[field] for field in all_db_fields if field in row.keys()} # 构建基础文档

                    # JSON 解析
                    json_field_names = ["platform_accounts", "identity", "personality", "sobriquets_by_group", "impression", "relationship_metrics"]
                    for field_name in json_field_names:
                        if field_name in doc and doc[field_name] is not None:
                            try:
                                doc[field_name] = json.loads(doc[field_name])
                            except json.JSONDecodeError:
                                logger.error(f"解析字段 '{field_name}' JSON 失败 for id '{profile_doc_id}'. 内容: {doc[field_name]}")
                                doc[field_name] = {} if field_name != "impression" else []
                    
                    # 处理特定 sobriquets_by_group 的投影
                    if projection and "sobriquets_by_group" in doc:
                        specific_sobriquet_group_key = None
                        for key_proj in projection.keys():
                            if key_proj.startswith("sobriquets_by_group.") and key_proj.endswith(".sobriquets"):
                                parts = key_proj.split('.')
                                if len(parts) == 3:
                                    specific_sobriquet_group_key = parts[1]
                                    break
                        
                        if specific_sobriquet_group_key:
                            full_sobriquets_data = doc.get("sobriquets_by_group", {})
                            if isinstance(full_sobriquets_data, dict):
                                doc["sobriquets_by_group"] = {
                                    specific_sobriquet_group_key: full_sobriquets_data.get(specific_sobriquet_group_key, {"sobriquets": []})
                                }
                            else:
                                doc["sobriquets_by_group"] = {}
                    
                    return doc
                except sqlite3.Error as e:
                    logger.error(f"获取投影文档时 SQLite 错误 (id '{profile_doc_id}'): {e}", exc_info=True)
                    return None
                finally:
                    conn.close()
        return await asyncio.to_thread(_sync_get_doc_for_projection)

