# profile/profile_db.py
import sqlite3
import json
import threading
import asyncio
from typing import Optional, List, Dict, Any
import datetime

# 从 stubs 导入
from stubs.mock_dependencies import get_logger 

logger = get_logger("ProfileDB_SQLite")

class ProfileDB:
    """
    处理与用户画像（profile_info）相关的数据库操作 (SQLite)。
    负责 profile_info 表和 platform_user_accounts 表的创建、读写等。
    profile_info._id 是 NaturalPersonID。
    platform_user_accounts 存储用户在不同平台的账户信息。
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
                # 修改 profile_info 表，移除 platform_accounts 字段
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_info (
                    _id TEXT PRIMARY KEY,
                    person_info_pid_ref TEXT,
                    -- platform_accounts TEXT, -- 已移除，改为关系存储
                    identity TEXT, 
                    personality TEXT, 
                    sobriquets_by_group TEXT, 
                    impression TEXT,
                    relationship_metrics TEXT,
                    creation_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_updated_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """)
                
                # 创建新的 platform_user_accounts 表
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS platform_user_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_document_id TEXT NOT NULL,
                    platform_name TEXT NOT NULL,
                    platform_user_id TEXT NOT NULL,
                    FOREIGN KEY (profile_document_id) REFERENCES profile_info(_id) ON DELETE CASCADE,
                    UNIQUE (profile_document_id, platform_name, platform_user_id) -- 确保唯一性
                )
                """)
                conn.commit()
                logger.info("表 'profile_info' (移除了 platform_accounts) 和 'platform_user_accounts' 已检查/创建。")
            except sqlite3.Error as e:
                logger.error(f"创建表时发生 SQLite 错误: {e}", exc_info=True)
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
        确保 profile_info 文档存在，并根据提供的 platform 和 platform_user_id 添加平台账户信息。
        """
        if not profile_document_id:
            logger.error("profile_document_id 为空，无法确保文档存在。")
            return False

        def _sync_ensure_doc_and_account():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    cursor = conn.cursor()
                    # 1. 确保 profile_info 文档存在
                    cursor.execute("SELECT _id FROM profile_info WHERE _id = ?", (profile_document_id,))
                    existing_doc = cursor.fetchone()

                    if not existing_doc:
                        initial_identity = json.dumps({}) 
                        initial_personality = json.dumps({}) 
                        initial_sobriquets_by_group = json.dumps({}) 
                        initial_impression = json.dumps([]) 
                        initial_relationship_metrics = json.dumps({})

                        cursor.execute("""
                        INSERT INTO profile_info (
                            _id, person_info_pid_ref, 
                            identity, personality, 
                            sobriquets_by_group, impression, relationship_metrics,
                            creation_timestamp, last_updated_timestamp 
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """, (
                            profile_document_id, person_info_pid_ref,
                            initial_identity, initial_personality,
                            initial_sobriquets_by_group, initial_impression, initial_relationship_metrics,
                        ))
                        logger.debug(f"为 profile_document_id '{profile_document_id}' 创建了新的 profile_info 记录。")
                    
                    # 2. 如果提供了平台和用户ID，则添加到 platform_user_accounts 表
                    if platform and platform_user_id:
                        platform_user_id_str = str(platform_user_id)
                        try:
                            cursor.execute("""
                            INSERT INTO platform_user_accounts (profile_document_id, platform_name, platform_user_id)
                            VALUES (?, ?, ?)
                            """, (profile_document_id, platform, platform_user_id_str))
                            logger.debug(f"为 profile_document_id '{profile_document_id}' 在平台 '{platform}' 添加了 platform_user_id '{platform_user_id_str}'。")
                        except sqlite3.IntegrityError: # 捕获 UNIQUE 约束冲突
                            logger.debug(f"平台账户 ({profile_document_id}, {platform}, {platform_user_id_str}) 已存在，未重复添加。")
                        # 更新 profile_info 的 last_updated_timestamp (因为关联数据发生变化)
                        cursor.execute("""
                        UPDATE profile_info 
                        SET last_updated_timestamp = CURRENT_TIMESTAMP 
                        WHERE _id = ?
                        """, (profile_document_id,))

                    conn.commit()
                    return True
                except sqlite3.Error as e:
                    logger.error(f"确保 profile_info 文档和平台账户时发生 SQLite 错误 (profile_id '{profile_document_id}'): {e}", exc_info=True)
                    conn.rollback()
                    return False
                finally:
                    conn.close()
        return await asyncio.to_thread(_sync_ensure_doc_and_account)


    async def get_profile_document(self, profile_document_id: str, fields: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        if not profile_document_id:
            return None

        def _sync_get_doc():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    cursor = conn.cursor()
                    
                    # 1. 获取 profile_info 表的数据
                    # 确定要从 profile_info 表查询的字段
                    profile_info_query_fields_str = "*"
                    profile_info_fields_to_select = []
                    
                    # 获取 profile_info 表的所有列名
                    cursor.execute(f"PRAGMA table_info(profile_info);")
                    profile_info_all_db_cols = [row['name'] for row in cursor.fetchall()]

                    if fields:
                        # 如果指定了 fields，只选择那些存在于 profile_info 表中的字段
                        profile_info_fields_to_select = [f for f in fields if f in profile_info_all_db_cols]
                        if "_id" not in profile_info_fields_to_select:
                             profile_info_fields_to_select.insert(0, "_id")
                        if not profile_info_fields_to_select : # 如果过滤后为空，但可能需要 platform_accounts
                            if "platform_accounts" in fields: # 如果只请求 platform_accounts
                                profile_info_fields_to_select.append("_id") # 至少需要_id来关联
                            else: # 如果请求的字段都不在 profile_info 且不含 platform_accounts
                                 profile_info_fields_to_select = ["_id"] # 默认取 _id
                        profile_info_query_fields_str = ", ".join(profile_info_fields_to_select)
                    else: # 获取所有字段
                        profile_info_fields_to_select = profile_info_all_db_cols
                        profile_info_query_fields_str = "*"
                    
                    cursor.execute(f"SELECT {profile_info_query_fields_str} FROM profile_info WHERE _id = ?", (profile_document_id,))
                    row = cursor.fetchone()

                    if not row:
                        return None

                    doc = dict(row)
                    
                    # 2. 获取并重构 platform_accounts 数据 (如果需要)
                    # 无论是否在 fields 中明确指定 "platform_accounts"，如果 fields 为 None (即获取所有)，则应包含它
                    # 或者如果 fields 中明确包含 "platform_accounts"
                    should_fetch_platform_accounts = (fields is None) or ("platform_accounts" in fields)

                    if should_fetch_platform_accounts:
                        cursor.execute("SELECT platform_name, platform_user_id FROM platform_user_accounts WHERE profile_document_id = ?", (profile_document_id,))
                        accounts_rows = cursor.fetchall()
                        platform_accounts_data: Dict[str, List[str]] = {}
                        for acc_row in accounts_rows:
                            p_name = acc_row["platform_name"]
                            p_uid = acc_row["platform_user_id"]
                            if p_name not in platform_accounts_data:
                                platform_accounts_data[p_name] = []
                            # 避免在列表中添加重复的 platform_user_id (尽管DB层面有UNIQUE约束)
                            if p_uid not in platform_accounts_data[p_name]:
                                platform_accounts_data[p_name].append(p_uid)
                        doc["platform_accounts"] = platform_accounts_data

                    # 3. 解析其他 JSON 字段
                    json_field_names = ["identity", "personality", 
                                        "sobriquets_by_group", "impression", "relationship_metrics"]
                    for field_name in json_field_names:
                        if field_name in doc and doc[field_name] is not None: # 确保字段存在于doc中 (可能因projection被排除)
                            try:
                                doc[field_name] = json.loads(doc[field_name])
                            except json.JSONDecodeError:
                                logger.error(f"获取文档时解析字段 '{field_name}' JSON 失败 for id '{profile_document_id}'. 内容: {doc[field_name]}")
                                doc[field_name] = {} if field_name != "impression" else []
                    
                    # 4. 如果指定了 fields，只返回请求的字段 (包括可能已重构的 platform_accounts)
                    if fields:
                        final_doc = {}
                        for f_name in fields:
                            if f_name in doc:
                                final_doc[f_name] = doc[f_name]
                        # 确保 _id 总是存在 (如果最初未请求但内部添加了)
                        if "_id" not in final_doc and "_id" in doc:
                            final_doc["_id"] = doc["_id"]
                        return final_doc
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
                    # platform_accounts 不再通过此方法更新
                    valid_fields = ["person_info_pid_ref", "identity", "personality", 
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
                        return True # 或者 False，取决于期望行为

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
        # 确保 field_name 不是 platform_accounts
        if field_name == "platform_accounts":
            logger.error("platform_accounts 不能通过 update_profile_field 更新，请使用特定账户管理方法。")
            return False
        return await self.update_profile_fields(profile_document_id, {field_name: field_value})
    
    async def get_profile_field(self, profile_document_id: str, field_name: str) -> Optional[Any]:
        doc = await self.get_profile_document(profile_document_id, fields=[field_name])
        return doc.get(field_name) if doc else None

    async def update_group_sobriquet_count(self, profile_document_id: str, platform: str, group_id_str: str, sobriquet_name: str) -> bool:
        # 此方法逻辑不变，因为它操作的是 sobriquets_by_group 字段
        if not profile_document_id:
            logger.error("profile_document_id 为空，无法更新绰号计数。")
            return False
        group_key = f"{platform}-{group_id_str}"
        def _sync_update_sobriquet():
            with self._lock:
                conn = self._get_connection_sync()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT sobriquets_by_group FROM profile_info WHERE _id = ?", (profile_document_id,))
                    row = cursor.fetchone()
                    if not row:
                        logger.error(f"更新绰号计数失败：未找到 profile_document_id '{profile_document_id}' 的记录。")
                        return False 
                    sobriquets_by_group_json_str = row["sobriquets_by_group"]
                    sobriquets_by_group_data = {}
                    if sobriquets_by_group_json_str:
                        try: sobriquets_by_group_data = json.loads(sobriquets_by_group_json_str)
                        except json.JSONDecodeError:
                            logger.error(f"解析 sobriquets_by_group JSON 失败: {sobriquets_by_group_json_str}")
                            sobriquets_by_group_data = {}
                    if group_key not in sobriquets_by_group_data:
                        sobriquets_by_group_data[group_key] = {"sobriquets": []}
                    if not isinstance(sobriquets_by_group_data[group_key].get("sobriquets"), list):
                        sobriquets_by_group_data[group_key]["sobriquets"] = []
                    group_sobriquets_list = sobriquets_by_group_data[group_key]["sobriquets"]
                    found_sobriquet = False
                    for item in group_sobriquets_list:
                        if isinstance(item, dict) and item.get("name") == sobriquet_name:
                            item["count"] = item.get("count", 0) + 1
                            found_sobriquet = True; break
                    if not found_sobriquet:
                        group_sobriquets_list.append({"name": sobriquet_name, "count": 1})
                    updated_json_str = json.dumps(sobriquets_by_group_data)
                    cursor.execute("UPDATE profile_info SET sobriquets_by_group = ?, last_updated_timestamp = CURRENT_TIMESTAMP WHERE _id = ?", 
                                   (updated_json_str, profile_document_id))
                    conn.commit()
                    logger.debug(f"为 profile_id '{profile_document_id}' 在群组 '{group_key}' 更新/添加绰号 '{sobriquet_name}'。")
                    return True
                except sqlite3.Error as e:
                    logger.error(f"更新群组绰号计数 SQLite 错误: {e}", exc_info=True); conn.rollback(); return False
                except Exception as e_generic:
                    logger.error(f"更新群组绰号计数一般错误: {e_generic}", exc_info=True); 
                    if conn: conn.rollback(); 
                    return False
                finally: 
                    if conn: conn.close()
        return await asyncio.to_thread(_sync_update_sobriquet)

    async def get_profile_document_for_find_projection(self, profile_doc_id: str, projection: Optional[Dict] = None) -> Optional[Dict]:
        # 此方法与 get_profile_document 类似，都需要处理 platform_accounts 的重构
        # 简化：直接调用 get_profile_document，然后应用投影（如果需要更细致的投影，则需复制并调整逻辑）
        
        # 确定投影是否请求了 platform_accounts 或其子内容，或者是否请求了所有内容
        requested_fields_for_get_doc = None
        if projection:
            requested_fields_for_get_doc = []
            includes_platform_accounts_related = False
            for key, value in projection.items():
                if value == 1:
                    if key == "platform_accounts" or key.startswith("platform_accounts."):
                        includes_platform_accounts_related = True
                    # 将 profile_info 表中的字段添加到请求列表
                    # (假设 profile_info 表的列名不包含'.')
                    if '.' not in key and key != "platform_accounts": # 排除 platform_accounts 本身，因为它会单独处理
                         requested_fields_for_get_doc.append(key)
            
            if includes_platform_accounts_related and "platform_accounts" not in requested_fields_for_get_doc:
                # 如果投影请求了 platform_accounts 相关内容，确保在调用 get_profile_document 时包含它
                # get_profile_document 内部会处理 platform_accounts 的获取
                requested_fields_for_get_doc.append("platform_accounts")
            
            if not requested_fields_for_get_doc and includes_platform_accounts_related:
                # 如果只请求了 platform_accounts，确保 _id 也被获取以用于关联
                 requested_fields_for_get_doc = ["_id", "platform_accounts"]
            elif not requested_fields_for_get_doc and not includes_platform_accounts_related and projection:
                 # 如果投影存在但没有有效字段被选中（例如只投影了不存在的字段）
                 # 至少获取_id
                 requested_fields_for_get_doc = ["_id"]


        full_doc = await self.get_profile_document(profile_doc_id, fields=requested_fields_for_get_doc)

        if not full_doc:
            return None

        if not projection: # 如果没有投影，返回完整文档（已包含重构的 platform_accounts）
            return full_doc

        # 应用投影逻辑
        projected_doc: Dict[str, Any] = {}
        for key, value in projection.items():
            if value == 1:
                if key in full_doc:
                    projected_doc[key] = full_doc[key]
                # 如果是 platform_accounts.platform_name 这种形式，目前 get_profile_document 
                # 返回的是完整的 platform_accounts 字典，应用层可以进一步处理。
                # SQLite 的 JSON 函数在这里用处不大，因为 platform_accounts 不再是 JSON 字符串。
        
        # 确保 _id 总是存在于投影结果中（如果原始文档有_id）
        if "_id" in full_doc and "_id" not in projected_doc:
            projected_doc["_id"] = full_doc["_id"]
            
        return projected_doc if projected_doc else None # 如果投影后为空但_id存在，则返回{_id: val}
