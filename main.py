import logging
import json
import time
import redis
from datetime import datetime, date, timedelta
from logging.handlers import RotatingFileHandler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# 导入项目核心模块
from core import AuthManager, TaskManager

# 配置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 创建格式化器
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')

import os

# 确保日志目录存在
os.makedirs('log', exist_ok=True)

# Redis配置 - 支持环境变量
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
REDIS_KEY = 'netease:music:data'

# 解析redis_url创建REDIS_CONF
try:
    # 解析Redis URL获取配置参数
    import urllib.parse
    parsed_url = urllib.parse.urlparse(REDIS_URL)
    
    REDIS_CONF = {
        'host': parsed_url.hostname or 'localhost',
        'port': parsed_url.port or 6379,
        'db': int(parsed_url.path.lstrip('/')) if parsed_url.path and parsed_url.path != '/' else 0,
        'password': parsed_url.password,
        'decode_responses': True
    }
    
    # 使用配置创建Redis连接
    redis_client = redis.Redis(**REDIS_CONF)
    # 测试连接
    redis_client.ping()
    logger.info("Redis连接成功")
except Exception as e:
    logger.error(f"Redis连接失败: {e}")
    redis_client = None

# 从环境变量获取配置
MAX_MONTHLY_SENDS = int(os.getenv('MAX_MONTHLY_SENDS', '4'))  # 每月最多发送次数
SEND_TIME = os.getenv('SEND_TIME', '09:30')  # 发送时间，格式：HH:MM
EXECUTION_INTERVAL_DAYS = int(os.getenv('EXECUTION_INTERVAL_DAYS', '7'))  # 执行间隔天数

# 创建文件处理器 - 带轮转功能
file_handler = RotatingFileHandler(
    'log/netease_music_cron.log',
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=3,  # 最多保留3个备份
    encoding='utf-8'
)
file_handler.setFormatter(formatter)

# 创建控制台处理器
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

# 添加处理器到 logger
logger.addHandler(file_handler)
logger.addHandler(stream_handler)


# Redis存储管理函数
def load_send_records():
    """从Redis加载发送记录"""
    if not redis_client:
        logger.error("Redis客户端未初始化，无法加载发送记录")
        return {}
    
    try:
        data = redis_client.get(REDIS_KEY)
        if data:
            return json.loads(data)
    except json.JSONDecodeError:
        logger.error("Redis中的数据不是有效的JSON格式")
    except Exception as e:
        logger.error(f"从Redis加载数据时发生错误: {e}")
    return {}

def save_send_records(data):
    """保存发送记录到Redis"""
    if not redis_client:
        logger.error("Redis客户端未初始化，无法保存发送记录")
        return False
    
    try:
        redis_client.set(REDIS_KEY, json.dumps(data, ensure_ascii=False))
        return True
    except Exception as e:
        logger.error(f"保存数据到Redis时发生错误: {e}")
        return False

def should_execute_task(user_uid):
    """检查是否应该执行任务，距离上次执行>=7天则返回True"""
    # 加载发送记录
    send_records = load_send_records()
    
    # 获取用户的最后发送记录
    user_record = send_records.get(str(user_uid), {})
    last_send_date_str = user_record.get('last_send_date')
    
    # 如果没有发送记录，则应该执行
    if not last_send_date_str:
        return True
    
    # 计算距离上次发送的天数
    try:
        last_send_date = datetime.strptime(last_send_date_str, '%Y-%m-%d').date()
        today = date.today()
        days_since_last_send = (today - last_send_date).days
        
        logger.info(f"用户 {user_uid} 距离上次执行已过去 {days_since_last_send} 天，设置的间隔为 {EXECUTION_INTERVAL_DAYS} 天")
        
        # 检查是否达到执行间隔
        return days_since_last_send >= EXECUTION_INTERVAL_DAYS
    except Exception as e:
        logger.error(f"计算执行时间间隔时发生错误: {e}")
        return False

def update_last_send_record(user_uid):
    """更新用户的最后发送记录"""
    send_records = load_send_records()
    today = date.today()
    today_str = today.strftime('%Y-%m-%d')
    
    send_records[str(user_uid)] = {
        'last_send_date': today_str,
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    if save_send_records(send_records):
        logger.info(f"已更新用户 {user_uid} 的最后发送记录到Redis: {today_str}")
    else:
        logger.error(f"更新用户 {user_uid} 的最后发送记录失败")

def daily_task_runner():
    """每日任务执行函数（日常签到、音乐人签到等）"""
    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始执行每日任务")
    
    try:
        # 初始化认证管理器
        auth = AuthManager()
        # 获取所有用户凭证
        user_list = auth.get_all_users_credentials()
        logger.info(f"发现 {len(user_list)} 个待处理用户")
        
        if not user_list:
            logger.info("没有待处理的用户，任务结束")
            return
        
        for user in user_list:
            try:
                client = None
                # 1. 尝试使用redis存的 Cookie
                if user['uid'] and str(user['uid']) != str(user['phone']):
                    client = auth.get_client_by_uid(user['uid'])
                
                # 2. 失败则登录
                if not client:
                    client = auth.login(user['phone'], user['password'], task_key=user['task_key'])
                
                if client:
                    logger.info(f"正在处理用户 {user['uid']} 的每日任务")
                    task = TaskManager(client)
                    
                    # 执行日常签到任务
                    daily_task_res = task.daily_task()
                    logger.info(f"日常签到任务结果：{json.dumps(daily_task_res, ensure_ascii=False)[:100]}")
                    
                    # 获取并执行音乐人签到任务
                    musician_cycle_missions_res = task.get_musician_cycle_mission()
                    if musician_cycle_missions_res.get('code') == 200:
                        musician_cycle_missions_data = musician_cycle_missions_res.get('data', {})
                        musician_cycle_missions_list = musician_cycle_missions_data.get('list', [])
                        for mission in musician_cycle_missions_list:
                            description = mission.get('description')
                            if "签到" not in description:
                                continue
                            logger.info(f"发现签到任务：{description}")
                            userMissionId = mission.get('userMissionId')
                            period = mission.get('period')
                            if userMissionId and period:
                                logger.info(f"{description}：userMissionId={userMissionId}, period={period}")
                                reward_obtain_res = task.reward_obtain(userMissionId, period)
                                logger.info(f"{description}结果：{json.dumps(reward_obtain_res, ensure_ascii=False)[:100]}")
                    else:
                        logger.error(f"获取音乐人循环任务失败：{json.dumps(musician_cycle_missions_res, ensure_ascii=False)[:100]}")
            except Exception as e:
                logger.error(f"处理用户 {user.get('uid')} 的每日任务时发生异常: {e}")
                continue
                
    except Exception as e:
        logger.error(f"每日任务执行异常: {e}")
    
    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 每日任务执行完毕")

def interval_task_runner():
    """间隔任务执行函数（音乐人发布动态任务）"""
    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始执行间隔任务")
    
    try:
        # 初始化认证管理器
        auth = AuthManager()
        # 获取所有用户凭证
        user_list = auth.get_all_users_credentials()
        logger.info(f"发现 {len(user_list)} 个待处理用户")
        
        if not user_list:
            logger.info("没有待处理的用户，任务结束")
            return
        
        for user in user_list:
            try:
                # 检查是否应该执行任务（距离上次执行>=设置的间隔天数）
                user_uid = user.get('uid', user.get('phone'))
                if not should_execute_task(user_uid):
                    # 计算预计下次执行时间
                    send_records = load_send_records()
                    user_record = send_records.get(str(user_uid), {})
                    last_send_date_str = user_record.get('last_send_date')
                    if last_send_date_str:
                        try:
                            last_send_date = datetime.strptime(last_send_date_str, '%Y-%m-%d').date()
                            today = date.today()
                            days_remaining = max(0, EXECUTION_INTERVAL_DAYS - (today - last_send_date).days)
                            next_execution_date = today + timedelta(days=days_remaining)
                            next_execution_time = f"{next_execution_date.strftime('%Y-%m-%d')} {SEND_TIME}"
                        except Exception as e:
                            logger.error(f"计算预计下次执行时间时发生错误: {e}")
                            next_execution_time = "未知"
                    else:
                        next_execution_time = "下次定时检查时"
                    
                    logger.info(f"用户 {user_uid} 距离上次执行不足 {EXECUTION_INTERVAL_DAYS} 天，跳过本次发布动态任务，预计下次执行时间{next_execution_time}")
                    continue
                
                client = None
                # 1. 尝试使用redis存的 Cookie
                if user['uid'] and str(user['uid']) != str(user['phone']):
                    client = auth.get_client_by_uid(user['uid'])
                
                # 2. 失败则登录
                if not client:
                    client = auth.login(user['phone'], user['password'], task_key=user['task_key'])
                
                if client:
                    logger.info(f"正在处理用户 {user['uid']} 的发布动态任务")
                    task = TaskManager(client)
                    share_res = task.share_song()
                    if share_res.get('code') == 200:
                        logger.info(f"分享成功：{json.dumps(share_res, ensure_ascii=False)[:100]}")
                        
                        # 更新最后发送记录
                        update_last_send_record(user_uid)
                        
                        event_id = share_res.get('event', {}).get('id')
                        if event_id:
                            logger.info("等待 10 秒后删除动态")
                            time.sleep(10)
                            delete_res = task.delete_dynamic(event_id)
                            logger.info(f'删除动态结果: {delete_res}')
                    else:
                        logger.warning(f"分享失败：{json.dumps(share_res, ensure_ascii=False)[:100]}")
            except Exception as e:
                logger.error(f"处理用户 {user.get('uid')} 的发布动态任务时发生异常: {e}")
                continue
                
    except Exception as e:
        logger.error(f"间隔任务执行异常: {e}")
    
    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 间隔任务执行完毕")


def main():
    """主函数"""
    logger.info("网易云音乐人任务调度器启动")
    
    # 创建调度器
    scheduler = BlockingScheduler(timezone='Asia/Shanghai')
    
    # 从环境变量获取执行时间，格式：HH:MM
    hour, minute = map(int, SEND_TIME.split(':'))
    
    try:
        # 添加每日任务 - 每天在指定时间执行
        scheduler.add_job(
            func=daily_task_runner,
            trigger=CronTrigger(hour=hour, minute=minute, day_of_week='*'),
            id='netease_daily_task',
            name='网易云音乐每日任务',
            replace_existing=True
        )
        
        # 添加间隔任务 - 每天在指定时间检查，但只在满足间隔天数时执行
        scheduler.add_job(
            func=interval_task_runner,
            trigger=CronTrigger(hour=hour, minute=minute + 5, day_of_week='*'),  # 间隔5分钟执行，避免冲突
            id='netease_interval_task',
            name='网易云音乐人发布动态任务',
            replace_existing=True
        )
        
        logger.info(f"每日任务已添加，每天 {SEND_TIME} 执行")
        logger.info(f"间隔任务已添加，每天 {hour}:{minute + 5:02d} 执行检查，实际执行间隔：每 {EXECUTION_INTERVAL_DAYS} 天")
        logger.info("任务调度器已启动，按 Ctrl+C 停止")
        
        # 启动调度器
        scheduler.start()
        
    except KeyboardInterrupt:
        logger.info("接收到停止信号，正在关闭调度器...")
        scheduler.shutdown()
        logger.info("调度器已关闭")
    except Exception as e:
        logger.error(f"调度器启动失败: {e}")


if __name__ == '__main__':
    main()
