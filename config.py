"""
配置文件 - 统一管理所有配置项
支持从环境变量读取配置，提供默认值
"""
import os
import urllib.parse
import redis
import logging

# 使用标准 logging，避免循环导入
_logger = logging.getLogger('netease_music')

# ========== Redis 配置 ==========
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/5')
REDIS_KEY = 'netease:music:data'

# Redis连接池配置
REDIS_POOL = None
REDIS_CONF = None

# 解析redis_url创建REDIS_CONF和REDIS_POOL
def init_redis():
    """初始化Redis连接池"""
    global REDIS_POOL, REDIS_CONF
    
    try:
        # 解析Redis URL获取配置参数
        parsed_url = urllib.parse.urlparse(REDIS_URL)
        
        REDIS_CONF = {
            'host': parsed_url.hostname or 'localhost',
            'port': parsed_url.port or 6379,
            'db': int(parsed_url.path.lstrip('/')) if parsed_url.path and parsed_url.path != '/' else 0,
            'password': parsed_url.password,
            'decode_responses': True
        }
        
        # 创建连接池
        REDIS_POOL = redis.ConnectionPool(**REDIS_CONF)
        
        # 测试连接
        redis_conn = redis.Redis(connection_pool=REDIS_POOL)
        redis_conn.ping()
        _logger.info(f"成功连接到Redis: {REDIS_URL}")
        return True
    except Exception as e:
        _logger.error(f"Redis连接失败: {e}")
        # 使用默认本地配置
        REDIS_CONF = {
            'host': 'localhost',
            'port': 6379,
            'db': 0,
            'password': None,
            'decode_responses': True
        }
        # 即使失败也创建连接池，以便后续重试
        REDIS_POOL = redis.ConnectionPool(**REDIS_CONF)
        return False

# 初始化Redis连接池
init_redis()

# ========== 登录方式配置 ==========
# LOGIN_METHOD 可选：
# - 'api'        使用 /weapi/login/cellphone 接口登录（默认）
# - 'playwright' 不再走密码登录接口，只依赖 Playwright 网页登录生成的 Cookie
LOGIN_METHOD = os.getenv('LOGIN_METHOD', 'playwright').strip().lower()
if LOGIN_METHOD not in ('api', 'playwright'):
    _logger.warning(f"未知的 LOGIN_METHOD={LOGIN_METHOD}，已回退为 'playwright'")
    LOGIN_METHOD = 'playwright'

# ========== Playwright 配置 ==========
# Playwright profile 根目录（存 cookies/cache/localStorage 等）
PLAYWRIGHT_PROFILE_BASEDIR = os.getenv('PLAYWRIGHT_PROFILE_BASEDIR', '.playwright_profiles')
# 多账号是否隔离 profile（建议 True，避免多账号串 Cookie）
PLAYWRIGHT_PROFILE_PER_USER = os.getenv('PLAYWRIGHT_PROFILE_PER_USER', '1').strip() not in ('0', 'false', 'False')

# ========== 任务调度配置 ==========
MAX_MONTHLY_SENDS = int(os.getenv('MAX_MONTHLY_SENDS', '4'))  # 每月最多发送次数

def validate_send_time(send_time):
    """验证SEND_TIME格式和范围"""
    try:
        parts = send_time.split(':')
        if len(parts) != 2:
            raise ValueError(f"SEND_TIME格式错误：应为 HH:MM 格式，当前值：{send_time}")
        
        hour, minute = map(int, parts)
        
        if hour < 0 or hour > 23:
            raise ValueError(f"SEND_TIME小时数超出范围：应为 0-23，当前值：{hour}")
        
        if minute < 0 or minute > 59:
            raise ValueError(f"SEND_TIME分钟数超出范围：应为 0-59，当前值：{minute}")
        
        return hour, minute
    except ValueError as e:
        if "格式错误" in str(e) or "超出范围" in str(e):
            raise
        raise ValueError(f"SEND_TIME格式错误：应为 HH:MM 格式（例如 09:30），当前值：{send_time}") from e

# 获取并验证SEND_TIME
_send_time_raw = os.getenv('SEND_TIME', '09:30')
try:
    validate_send_time(_send_time_raw)
    SEND_TIME = _send_time_raw
except ValueError as e:
    _logger.error(f"配置错误：{e}")
    _logger.error(f"使用默认值 09:30")
    SEND_TIME = '09:30'  # 使用默认值

EXECUTION_INTERVAL_DAYS = int(os.getenv('EXECUTION_INTERVAL_DAYS', '7'))  # 执行间隔天数

