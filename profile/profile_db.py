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
                # 确保 sobriquets_by_group 字段存在且为 TEXT 类型，用于存储 JSON 字符串
                # 其他字段根据你的设计文档添加
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_info (
                    _id TEXT PRIMARY KEY,
                    person_info_pid_ref TEXT,
                    platform_accounts TEXT, 
                    identity TEXT,
                    personality TEXT,
                    sobriquets_by_group TEXT, -- 用于存储群组绰号信息，JSON 格式
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
        """
        确保数据库中存在指定 profile_document_id 的文档 (Upsert 逻辑)。
        如果文档不存在，则使用提供的信息创建它，并确保核心字段被初始化。
        """
        if not profile_document_id:
            logger.error("profile_document_id 为空，无法确保文档存在。")
            return False

        def _sync_ensure_doc():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT _id FROM profile_info WHERE _id = ?", (profile_document_id,))
                    existing_doc = cursor.fetchone()

                    if not existing_doc:
                        # 文档不存在，插入新记录
                        # 初始化 platform_accounts 和 sobriquets_by_group 为空的 JSON 对象字符串
                        # 其他 JSON 字段也类似初始化
                        initial_platform_accounts = json.dumps({})
                        initial_identity = json.dumps({})
                        initial_personality = json.dumps({})
                        initial_sobriquets_by_group = json.dumps({}) # 关键：初始化为空的JSON对象
                        initial_impression = json.dumps([]) # impression 通常是列表
                        initial_relationship_metrics = json.dumps({})

                        cursor.execute("""
                        INSERT INTO profile_info (
                            _id, person_info_pid_ref, 
                            platform_accounts, identity, personality, 
                            sobriquets_by_group, impression, relationship_metrics,
                            creation_timestamp, last_updated_timestamp 
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """, (
                            profile_document_id, person_info_pid_ref,
                            initial_platform_accounts, initial_identity, initial_personality,
                            initial_sobriquets_by_group, initial_impression, initial_relationship_metrics,
                        ))
                        logger.debug(f"为 profile_document_id '{profile_document_id}' (pid_ref: {person_info_pid_ref}) 创建了新的 profile_info 记录。")
                        created_new = True
                    else:
                        created_new = False
                    
                    # 如果平台和用户ID提供了，更新 platform_accounts
                    if platform and platform_user_id:
                        cursor.execute("SELECT platform_accounts FROM profile_info WHERE _id = ?", (profile_document_id,))
                        row = cursor.fetchone()
                        current_platform_accounts = {}
                        if row and row["platform_accounts"]:
                            try:
                                current_platform_accounts = json.loads(row["platform_accounts"])
                            except json.JSONDecodeError:
                                logger.error(f"解析 profile_id '{profile_document_id}' 的 platform_accounts JSON 失败: {row['platform_accounts']}")
                                current_platform_accounts = {} # 出错时重置

                        platform_user_id_str = str(platform_user_id)
                        if platform not in current_platform_accounts:
                            current_platform_accounts[platform] = []
                        
                        if platform_user_id_str not in current_platform_accounts[platform]:
                            current_platform_accounts[platform].append(platform_user_id_str)
                            cursor.execute("""
                            UPDATE profile_info
                            SET platform_accounts = ?, last_updated_timestamp = CURRENT_TIMESTAMP
                            WHERE _id = ?
                            """, (json.dumps(current_platform_accounts), profile_document_id))
                            logger.debug(f"为 profile_document_id '{profile_document_id}' 的平台 '{platform}' 添加/更新了 platform_user_id '{platform_user_id_str}'。")
                    
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
                        effective_fields_to_select = list(fields) 
                        if "_id" not in effective_fields_to_select:
                            effective_fields_to_select.insert(0, "_id") 
                        query_fields = ", ".join(effective_fields_to_select)
                    else: 
                        cursor.execute(f"PRAGMA table_info(profile_info);")
                        effective_fields_to_select = [row['name'] for row in cursor.fetchall()]
                        query_fields = "*"


                    cursor.execute(f"SELECT {query_fields} FROM profile_info WHERE _id = ?", (profile_document_id,))
                    row = cursor.fetchone()

                    if not row:
                        return None

                    doc = dict(row)
                    # 定义需要进行JSON解析的字段
                    json_field_names = ["platform_accounts", "identity", "personality", 
                                        "sobriquets_by_group", "impression", "relationship_metrics"]
                    for field_name in json_field_names:
                        if field_name in doc and doc[field_name] is not None:
                            try:
                                doc[field_name] = json.loads(doc[field_name])
                            except json.JSONDecodeError:
                                logger.error(f"获取文档时解析字段 '{field_name}' JSON 失败 for id '{profile_document_id}'. 内容: {doc[field_name]}")
                                # 根据字段的预期结构提供默认值
                                if field_name == "impression":
                                    doc[field_name] = []
                                else:
                                    doc[field_name] = {}
                    
                    if fields: 
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
                    # 确保这里的字段列表与表结构一致
                    valid_fields = ["person_info_pid_ref", "platform_accounts", "identity", "personality", 
                                    "sobriquets_by_group", "impression", "relationship_metrics"]

                    for field_name, field_value in updates.items():
                        if field_name not in valid_fields:
                            logger.warning(f"尝试更新无效字段 '{field_name}' for id '{profile_document_id}'。已跳过。")
                            continue
                        
                        set_clauses.append(f"{field_name} = ?")
                        if isinstance(field_value, (dict, list)): # 如果值是字典或列表，序列化为JSON字符串
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
        """
        更新 profile_document_id 文档中特定群组的绰号计数，或添加新条目。
        这个方法会读取 sobriquets_by_group 字段 (JSON 字符串), 解析它, 更新它, 然后写回。
        """
        if not profile_document_id:
            logger.error("profile_document_id 为空，无法更新绰号计数。")
            return False

        group_key = f"{platform}-{group_id_str}"

        def _sync_update_sobriquet():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    cursor = conn.cursor()
                    # 1. 获取当前的 sobriquets_by_group 数据
                    cursor.execute("SELECT sobriquets_by_group FROM profile_info WHERE _id = ?", (profile_document_id,))
                    row = cursor.fetchone()

                    if not row:
                        logger.error(f"更新绰号计数失败：未找到 profile_document_id '{profile_document_id}' 的记录。请先调用 ensure_profile_document_exists。")
                        return False 

                    sobriquets_by_group_json_str = row["sobriquets_by_group"]
                    sobriquets_by_group_data = {}
                    if sobriquets_by_group_json_str:
                        try:
                            sobriquets_by_group_data = json.loads(sobriquets_by_group_json_str)
                        except json.JSONDecodeError:
                            logger.error(f"解析 profile_id '{profile_document_id}' 的 sobriquets_by_group JSON 失败: {sobriquets_by_group_json_str}")
                            # 如果解析失败，可以根据策略决定是重置还是报错。这里选择重置为空字典。
                            sobriquets_by_group_data = {}
                    
                    # 2. 更新数据
                    if group_key not in sobriquets_by_group_data:
                        sobriquets_by_group_data[group_key] = {"sobriquets": []}
                    
                    # 确保 "sobriquets" 键存在且其值为列表
                    if not isinstance(sobriquets_by_group_data[group_key].get("sobriquets"), list):
                        sobriquets_by_group_data[group_key]["sobriquets"] = []
                        
                    group_sobriquets_list = sobriquets_by_group_data[group_key]["sobriquets"]
                    
                    found_sobriquet = False
                    for item in group_sobriquets_list:
                        if isinstance(item, dict) and item.get("name") == sobriquet_name:
                            item["count"] = item.get("count", 0) + 1
                            found_sobriquet = True
                            break
                    
                    if not found_sobriquet:
                        group_sobriquets_list.append({"name": sobriquet_name, "count": 1})
                    
                    # sobriquets_by_group_data[group_key]["sobriquets"] 已经被原地修改了 (如果它是列表)
                    # 如果之前不是列表而是被重置了，这里也正确

                    # 3. 将更新后的数据写回数据库
                    updated_sobriquets_by_group_json_str = json.dumps(sobriquets_by_group_data)
                    cursor.execute("""
                    UPDATE profile_info
                    SET sobriquets_by_group = ?, last_updated_timestamp = CURRENT_TIMESTAMP
                    WHERE _id = ?
                    """, (updated_sobriquets_by_group_json_str, profile_document_id))
                    
                    conn.commit()
                    logger.debug(f"为 profile_id '{profile_document_id}' 在群组 '{group_key}' 更新/添加绰号 '{sobriquet_name}'。")
                    return True

                except sqlite3.Error as e:
                    logger.error(f"更新群组绰号计数时发生 SQLite 错误 (profile_id '{profile_document_id}', group '{group_key}', sobriquet '{sobriquet_name}'): {e}", exc_info=True)
                    conn.rollback()
                    return False
                except Exception as e_generic: # 捕获其他潜在错误，如JSON操作错误
                    logger.error(f"更新群组绰号计数时发生一般错误 (profile_id '{profile_document_id}', group '{group_key}', sobriquet '{sobriquet_name}'): {e_generic}", exc_info=True)
                    if conn: conn.rollback() # 确保回滚
                    return False
                finally:
                    if conn: conn.close()
        return await asyncio.to_thread(_sync_update_sobriquet)

    async def get_profile_document_for_find_projection(self, profile_doc_id: str, projection: Optional[Dict] = None) -> Optional[Dict]:
        """
        模拟 MongoDB 的 find + projection 行为，主要用于适配 ProfileManager 的旧逻辑。
        对于 SQLite，这通常意味着选择特定列，并对 JSON 字段进行后处理。
        """
        def _sync_get_doc_for_projection():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    # 确定需要查询的顶级字段
                    fields_to_fetch_set = set()
                    if projection:
                        for key, value in projection.items():
                            if value == 1: # MongoDB-style projection {field: 1}
                                top_level_field = key.split('.')[0] # 例如 "sobriquets_by_group.group1.sobriquets" -> "sobriquets_by_group"
                                fields_to_fetch_set.add(top_level_field)
                    
                    if not fields_to_fetch_set: # 如果没有指定投影，或投影为空，则获取所有
                        cursor = conn.cursor()
                        cursor.execute(f"PRAGMA table_info(profile_info);")
                        all_db_fields = [row['name'] for row in cursor.fetchall()]
                        query_fields_str = "*"
                    else:
                        if "_id" not in fields_to_fetch_set : # 确保 _id 总是被选择
                            fields_to_fetch_set.add("_id")
                        all_db_fields = list(fields_to_fetch_set) # 这些是我们将从数据库选择的列
                        query_fields_str = ", ".join(all_db_fields)

                    cursor = conn.cursor()
                    cursor.execute(f"SELECT {query_fields_str} FROM profile_info WHERE _id = ?", (profile_doc_id,))
                    row = cursor.fetchone()

                    if not row:
                        return None

                    doc = {field: row[field] for field in all_db_fields if field in row.keys()}

                    # JSON 解析 (与 get_profile_document 类似)
                    json_field_names = ["platform_accounts", "identity", "personality", 
                                        "sobriquets_by_group", "impression", "relationship_metrics"]
                    for field_name in json_field_names:
                        if field_name in doc and doc[field_name] is not None:
                            try:
                                doc[field_name] = json.loads(doc[field_name])
                            except json.JSONDecodeError:
                                logger.error(f"投影时解析字段 '{field_name}' JSON 失败 for id '{profile_doc_id}'. 内容: {doc[field_name]}")
                                doc[field_name] = {} if field_name != "impression" else []
                    
                    # 处理 sobriquets_by_group 的特定子字段投影 (如果需要)
                    # 例如，如果 projection 是 {"sobriquets_by_group.platform-group1.sobriquets": 1}
                    if projection and "sobriquets_by_group" in doc and isinstance(doc["sobriquets_by_group"], dict):
                        projected_sobriquets_data = {}
                        needs_sobriquet_projection_filtering = False
                        
                        for proj_key in projection.keys():
                            if proj_key.startswith("sobriquets_by_group.") and projection[proj_key] == 1:
                                needs_sobriquet_projection_filtering = True
                                parts = proj_key.split('.') # e.g., "sobriquets_by_group", "platform-group1", "sobriquets"
                                if len(parts) >= 2: # 至少需要到群组级别
                                    group_key_from_proj = parts[1]
                                    if group_key_from_proj in doc["sobriquets_by_group"]:
                                        if len(parts) == 2: # sobriquets_by_group.group_key
                                            projected_sobriquets_data[group_key_from_proj] = doc["sobriquets_by_group"][group_key_from_proj]
                                        elif len(parts) == 3 and parts[2] == "sobriquets": # sobriquets_by_group.group_key.sobriquets
                                            projected_sobriquets_data[group_key_from_proj] = {
                                                "sobriquets": doc["sobriquets_by_group"][group_key_from_proj].get("sobriquets", [])
                                            }
                        if needs_sobriquet_projection_filtering:
                             doc["sobriquets_by_group"] = projected_sobriquets_data if projected_sobriquets_data else {}
                    
                    # 如果原始 projection 只要求顶级字段，则不需要进一步过滤已解析的 JSON
                    # 如果 projection 更细致 (如 a.b.c:1)，则需要在这里处理，但 SQLite 的 JSON 支持有限，通常在应用层处理
                    return doc
                except sqlite3.Error as e:
                    logger.error(f"获取投影文档时 SQLite 错误 (id '{profile_doc_id}'): {e}", exc_info=True)
                    return None
                finally:
                    conn.close()
        return await asyncio.to_thread(_sync_get_doc_for_projection)
