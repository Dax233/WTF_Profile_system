# stubs/mock_config.py
# 模拟全局配置

class ProfileConfig:
    def __init__(self):
        self.enable_sobriquet_mapping = True
        self.sobriquet_analysis_probability = 1.0
        self.sobriquet_analysis_history_limit = 20
        self.max_sobriquets_in_prompt = 5
        self.sobriquet_probability_smoothing = 0.1
        self.recent_speakers_limit_for_injection = 5
        self.sobriquet_queue_max_size = 100
        self.sobriquet_process_sleep_interval = 1.0 # 处理队列的休眠间隔
        self.error_sleep_interval = 5 # 出错时的休眠间隔
        self.sobriquet_min_length = 1
        self.sobriquet_max_length = 15
        self.profile_info_collection_name = "profile_info" # 在SQLite中这将是表名
        self.db_path = "profile.db" # SQLite数据库文件路径

class ModelConfig:
    def __init__(self, name="mock_llm", temp=0.5, max_tokens=256):
        self.config = {
            "name": name,
            "temp": temp,
            "max_tokens": max_tokens
        }

    def get(self, key, default=None):
        return self.config.get(key, default)

class SecurityConfig:
    def __init__(self):
        self.profile_id_salt = "test_salt_for_profile_id"

class BotConfig:
    def __init__(self):
        self.qq_account = "mock_bot_qq_10000"
        self.nickname = "测试机器人"

class GlobalConfig:
    def __init__(self):
        self.profile = ProfileConfig()
        self.model = {
            "sobriquet_mapping": ModelConfig(name="sobriquet_llm_mock") 
        }
        self.security = SecurityConfig()
        self.bot = BotConfig()

    def get(self, key_path, default=None):
        # 简单的 get 方法，用于模拟 global_config.profile.get("key", "default")
        # 例如 key_path = "profile.db_path"
        keys = key_path.split('.')
        val = self
        try:
            for key in keys:
                if isinstance(val, dict):
                    val = val.get(key)
                else:
                    val = getattr(val, key)
            return val if val is not None else default
        except (AttributeError, KeyError):
            return default

global_config = GlobalConfig()

# print(f"数据库路径: {global_config.profile.db_path}")
# print(f"盐: {global_config.security.profile_id_salt}")
# print(f"LLM模型配置: {global_config.model.get('sobriquet_mapping').get('name')}")
