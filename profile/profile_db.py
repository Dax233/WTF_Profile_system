# profile/profile_db.py
import sqlite3
import json
import threading
from typing import Optional, List, Dict, Any
import datetime

# 从 stubs 导入
from stubs.mock_dependencies import get_logger # 假设 get_logger 在 stubs 中

logger = get_logger("ProfileDB_SQLite")

class ProfileDB:
    """
    处理与用户画像（profile_info）相关的数据库操作 (SQLite)。
    负责 profile_info 表的创建、读写等。
    文档的 _id 是 NaturalPersonID。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        # SQLite 对于并发写入操作，最好在连接级别或操作级别加锁。
        # self._lock = threading.Lock() 
        # 更推荐的做法是为每个线程创建独立的连接，或者确保连接不在线程间共享而不加锁。
        # 对于简单的测试和串行操作，或者短时操作，每次获取新连接是可行的。
        # 如果 ProfileManager 和 SobriquetManager 在不同线程中操作，需要注意并发问题。
        # 当前的 SobriquetManager 使用了后台线程，因此锁是必要的，或者确保后台线程使用独立连接。
        # 这里我们使用一个实例级别的锁来简化。
        self._lock = threading.RLock() # 可重入锁
        self._create_tables_if_not_exists()
        logger.info(f"ProfileDB_SQLite 初始化成功，使用数据库文件: '{db_path}'")

    def _get_connection(self):
        # 每次都创建新连接，对于 SQLite 来说是线程安全的，只要不在线程间共享连接对象。
        # 如果要共享连接，则需要外部同步。
        conn = sqlite3.connect(self.db_path, timeout=10) # 增加超时
        conn.row_factory = sqlite3.Row # 方便通过列名访问数据
        return conn

    def _create_tables_if_not_exists(self):
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # profile_info 表结构基于 profile.md
                # NaturalPersonID 作为主键 (_id)
                # platform_accounts, sobriquets_by_group, identity, personality, impression, relationship_metrics 将存储为 JSON 字符串
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_info (
                    _id TEXT PRIMARY KEY,
                    person_info_pid_ref TEXT,      -- 过渡阶段使用，最终移除
                    platform_accounts TEXT,        -- JSON: {"platform_name": ["user_id_a", ...]}
                    identity TEXT,                 -- JSON: {"cultural_background": "...", ...}
                    personality TEXT,              -- JSON: {"openness": 0.7, ...} or {"tags": ["乐观"]}
                    sobriquets_by_group TEXT,      -- JSON: {"platform-group_id": {"sobriquets": [{"name": "绰号A", "count": 10}]}}
                    impression TEXT,               -- JSON: ["乐于助人", {"event": "上次讨论了AI技术", "timestamp": ...}]
                    relationship_metrics TEXT,     -- JSON: {"familiarity": 0.8, ...} (未来整合)
                    creation_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_updated_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """)
                # 可以考虑为经常查询的字段添加索引，例如 person_info_pid_ref (如果查询频繁)
                # cursor.execute("CREATE INDEX IF NOT EXISTS idx_person_info_pid_ref ON profile_info(person_info_pid_ref);")
                conn.commit()
                logger.info("表 'profile_info' 已检查/创建。")
            except sqlite3.Error as e:
                logger.error(f"创建表 'profile_info' 时发生 SQLite 错误: {e}", exc_info=True)
                raise
            finally:
                conn.close()

    def is_available(self) -> bool:
        return True # 简化，假设构造成功即为可用

    def ensure_profile_document_exists(self,
                                     profile_document_id: str,
                                     person_info_pid_ref: Optional[str] = None,
                                     platform: Optional[str] = None,
                                     platform_user_id: Optional[str] = None) -> bool:
        """
        确保 profile_info 中存在指定 profile_document_id 的文档。
        如果文档不存在，则创建它，并初始化核心字段。
        如果提供了 platform 和 platform_user_id，则会尝试更新 platform_accounts。
        返回 True 如果文档被创建或已存在，False 如果发生错误。
        """
        if not profile_document_id:
            logger.error("profile_document_id 为空，无法确保文档存在。")
            return False

        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # 尝试插入新文档，如果已存在则忽略
                # 初始化所有 JSON 类型的字段为空 JSON 对象字符串 '{}'
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
                    json.dumps({}), json.dumps([]), json.dumps({}), # impression 初始化为空列表
                ))
                
                created_new = cursor.rowcount > 0
                if created_new:
                    logger.debug(f"为 profile_document_id '{profile_document_id}' (pid_ref: {person_info_pid_ref}) 创建了新的 profile_info 记录。")
                
                # 如果提供了平台和用户ID，更新 platform_accounts
                if platform and platform_user_id:
                    cursor.execute("SELECT platform_accounts FROM profile_info WHERE _id = ?", (profile_document_id,))
                    row = cursor.fetchone()
                    current_platform_accounts = {}
                    if row and row["platform_accounts"]:
                        try:
                            current_platform_accounts = json.loads(row["platform_accounts"])
                        except json.JSONDecodeError:
                            logger.error(f"解析 profile_id '{profile_document_id}' 的 platform_accounts JSON 失败: {row['platform_accounts']}")
                            current_platform_accounts = {} # 重置

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

    def get_profile_document(self, profile_document_id: str, fields: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """
        获取指定 profile_document_id 的完整文档或指定字段。
        JSON 字段会被解析为 Python 对象。
        """
        if not profile_document_id:
            return None

        with self._lock: # 虽然是读操作，但如果涉及到复杂的事务或多步骤读，加锁更安全
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                
                query_fields = "*"
                if fields:
                    # 确保 _id 总是在选择的字段中，如果用户没有明确指定
                    if "_id" not in fields:
                        fields_to_select = ["_id"] + fields
                    else:
                        fields_to_select = fields
                    query_fields = ", ".join(fields_to_select)

                cursor.execute(f"SELECT {query_fields} FROM profile_info WHERE _id = ?", (profile_document_id,))
                row = cursor.fetchone()

                if not row:
                    return None

                # 将 Row 对象转换为字典，并解析 JSON 字段
                doc = dict(row)
                json_field_names = ["platform_accounts", "identity", "personality", "sobriquets_by_group", "impression", "relationship_metrics"]
                for field_name in json_field_names:
                    if field_name in doc and doc[field_name] is not None:
                        try:
                            doc[field_name] = json.loads(doc[field_name])
                        except json.JSONDecodeError:
                            logger.error(f"获取文档时解析字段 '{field_name}' JSON 失败 for id '{profile_document_id}'. 内容: {doc[field_name]}")
                            doc[field_name] = {} if field_name != "impression" else [] # 保持默认类型
                
                # 如果用户指定了字段，只返回那些字段（确保 _id 也在）
                if fields:
                    return {k: doc[k] for k in fields_to_select if k in doc}
                return doc
            except sqlite3.Error as e:
                logger.error(f"获取 profile_info 文档时 SQLite 错误 (id '{profile_document_id}'): {e}", exc_info=True)
                return None
            finally:
                conn.close()

    def update_profile_field(self, profile_document_id: str, field_name: str, field_value: Any) -> bool:
        """
        更新 profile_info 文档中的单个顶级字段。
        如果 field_value 是字典或列表，它将被转换为 JSON 字符串存储。
        """
        return self.update_profile_fields(profile_document_id, {field_name: field_value})

    def update_profile_fields(self, profile_document_id: str, updates: Dict[str, Any]) -> bool:
        """
        更新 profile_info 文档中的多个顶级字段。
        字典或列表类型的值将被转换为 JSON 字符串存储。
        """
        if not profile_document_id or not updates:
            return False

        with self._lock:
            conn = self._get_connection()
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
                    return True # 没有有效字段也算成功（无操作）

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
    
    def get_profile_field(self, profile_document_id: str, field_name: str) -> Optional[Any]:
        """获取单个顶级字段的值，JSON字段会被解析。"""
        doc = self.get_profile_document(profile_document_id, fields=[field_name])
        return doc.get(field_name) if doc else None

    def update_group_sobriquet_count(self, profile_document_id: str, platform: str, group_id_str: str, sobriquet_name: str) -> bool:
        """
        更新特定群组的特定绰号的计数。
        这是 ProfileDB 中一个相对特定的方法，但由于其逻辑复杂性，暂时保留。
        """
        if not profile_document_id:
            logger.error("profile_document_id 为空，无法更新绰号计数。")
            return False

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
                    # 尝试确保文档存在，以防万一 (例如，如果调用此方法前没有调用 ensure_profile_document_exists)
                    if not self.ensure_profile_document_exists(profile_document_id): # 再次尝试确保文档存在
                         return False 
                    # 再次读取
                    cursor.execute("SELECT sobriquets_by_group FROM profile_info WHERE _id = ?", (profile_document_id,))
                    row = cursor.fetchone()
                    if not row: # 如果仍然没有，则确实有问题
                        logger.error(f"再次尝试后仍未找到 profile_document_id '{profile_document_id}' 的记录。")
                        return False


                sobriquets_by_group_data = {}
                if row["sobriquets_by_group"]:
                    try:
                        sobriquets_by_group_data = json.loads(row["sobriquets_by_group"])
                    except json.JSONDecodeError:
                        logger.error(f"解析 profile_id '{profile_document_id}' 的 sobriquets_by_group JSON 失败: {row['sobriquets_by_group']}")
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
                return True

            except sqlite3.Error as e:
                logger.error(f"更新群组绰号计数时发生 SQLite 错误 (profile_id '{profile_document_id}', group '{group_key}', sobriquet '{sobriquet_name}'): {e}", exc_info=True)
                conn.rollback()
                return False
            finally:
                conn.close()

    # 辅助方法，用于 ProfileManager 的 find 模拟 (如果仍然需要 MockDbCollection)
    # 或者 ProfileManager 直接调用 get_profile_document
    async def get_profile_document_for_find_projection(self, profile_doc_id: str, projection: Optional[Dict] = None) -> Optional[Dict]:
        """
        根据 profile_doc_id 获取文档，并根据 projection 模拟 MongoDB 的字段选择。
        这是为 MockDbCollection.find 提供的辅助方法，或者 ProfileManager 可以直接使用 get_profile_document。
        """
        if not projection:
            return self.get_profile_document(profile_doc_id)

        fields_to_select = []
        specific_sobriquet_group_key = None

        for key, value in projection.items():
            if value == 1: # MongoDB-style projection
                if key == "_id":
                    fields_to_select.append("_id")
                elif key.startswith("sobriquets_by_group.") and key.endswith(".sobriquets"):
                    # e.g., "sobriquets_by_group.qq-group123.sobriquets"
                    parts = key.split('.')
                    if len(parts) == 3:
                        specific_sobriquet_group_key = parts[1]
                        if "sobriquets_by_group" not in fields_to_select:
                             fields_to_select.append("sobriquets_by_group")
                elif key == "person_info_pid_ref": # 可选
                     if "person_info_pid_ref" not in fields_to_select:
                        fields_to_select.append("person_info_pid_ref")
                # 可以根据需要添加对其他投影字段的支持
                elif key in ["platform_accounts", "identity", "personality", "impression", "relationship_metrics", "sobriquets_by_group"]:
                    if key not in fields_to_select:
                        fields_to_select.append(key)


        if not fields_to_select : # 如果没有有效的投影字段，则获取所有
             doc = self.get_profile_document(profile_doc_id)
        else:
             doc = self.get_profile_document(profile_doc_id, fields=list(set(fields_to_select)))


        if not doc:
            return None

        # 如果请求了特定的 sobriquet group key，则只保留那部分数据
        if "sobriquets_by_group" in doc and specific_sobriquet_group_key:
            full_sobriquets_data = doc.get("sobriquets_by_group", {})
            if isinstance(full_sobriquets_data, dict): # 确保它是字典
                doc["sobriquets_by_group"] = {
                    specific_sobriquet_group_key: full_sobriquets_data.get(specific_sobriquet_group_key, {"sobriquets": []})
                }
            else: # 如果不是字典（例如，已经是错误的数据），则清空
                doc["sobriquets_by_group"] = {}
        
        # logger.debug(f"SQLite find projection result for {profile_doc_id}: {doc}")
        return doc

