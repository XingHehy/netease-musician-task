import logging
import json
import time
import redis
from datetime import datetime, date, timedelta
from logging.handlers import RotatingFileHandler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# 导入项目核心模块
from core import AuthManager, TaskManager, logger

# 从配置文件导入所有配置
from config import (
    REDIS_KEY, REDIS_CONF, REDIS_POOL,
    MAX_MONTHLY_SENDS, SEND_TIME, EXECUTION_INTERVAL_DAYS,
    LOGIN_METHOD,
    PLAYWRIGHT_PROFILE_BASEDIR, PLAYWRIGHT_PROFILE_PER_USER,
)

import os

# 确保日志目录存在
os.makedirs('log', exist_ok=True)

# 为 main.py 添加额外的 cron 日志文件处理器（如果还没有添加）
cron_log_file = 'log/netease_music_cron.log'
has_cron_handler = False
for h in logger.handlers:
    if isinstance(h, RotatingFileHandler):
        try:
            # 检查文件路径是否包含 cron 日志文件名
            if hasattr(h, 'baseFilename') and 'netease_music_cron.log' in h.baseFilename:
                has_cron_handler = True
                break
        except Exception:
            pass

if not has_cron_handler:
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    cron_file_handler = RotatingFileHandler(
        cron_log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=3,  # 最多保留3个备份
        encoding='utf-8'
    )
    cron_file_handler.setFormatter(formatter)
    logger.addHandler(cron_file_handler)

# 使用配置中的Redis连接池创建Redis客户端
# 注意：Redis连接日志已在config.py中记录，这里不再重复记录
try:
    if REDIS_POOL:
        redis_client = redis.Redis(connection_pool=REDIS_POOL)
        redis_client.ping()
    else:
        # 如果连接池未初始化，使用配置创建连接
        redis_client = redis.Redis(**REDIS_CONF) if REDIS_CONF else None
        if redis_client:
            redis_client.ping()
        else:
            logger.error("Redis配置未初始化")
            redis_client = None
except Exception as e:
    logger.error(f"Redis连接失败: {e}")
    redis_client = None


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
    """检查是否应该执行任务，距离上次执行>=7天且每月发送次数未达上限则返回True"""
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
                
        # 检查是否达到执行间隔
        if days_since_last_send < EXECUTION_INTERVAL_DAYS:
            return False
        
        # 检查每月发送次数是否超过上限
        current_year_month = today.strftime('%Y-%m')
        monthly_sends = user_record.get('monthly_sends', {})
        current_month_count = monthly_sends.get(current_year_month, 0)
        
        if current_month_count >= MAX_MONTHLY_SENDS:
            return False
        
        return True
    except Exception as e:
        logger.error(f"计算执行时间间隔或检查每月发送次数时发生错误: {e}")
        return False

def update_last_send_record(user_uid):
    """更新用户的最后发送记录和月度发送计数"""
    send_records = load_send_records()
    today = date.today()
    today_str = today.strftime('%Y-%m-%d')
    current_year_month = today.strftime('%Y-%m')
    
    # 获取用户现有记录，如果不存在则创建新记录
    user_record = send_records.get(str(user_uid), {})
    
    # 更新最后发送日期和更新时间
    user_record['last_send_date'] = today_str
    user_record['update_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 更新月度发送计数
    monthly_sends = user_record.get('monthly_sends', {})
    monthly_sends[current_year_month] = monthly_sends.get(current_year_month, 0) + 1
    user_record['monthly_sends'] = monthly_sends
    
    # 保存更新后的记录
    send_records[str(user_uid)] = user_record
    
    if save_send_records(send_records):
        logger.info(f"已更新用户 {user_uid} 的最后发送记录到Redis: {today_str}")
        logger.info(f"用户 {user_uid} {current_year_month} 月发送次数已更新为 {monthly_sends[current_year_month]}/{MAX_MONTHLY_SENDS}")
    else:
        logger.error(f"更新用户 {user_uid} 的最后发送记录失败")

def retry_with_backoff(func, max_retries=3, delay=2, task_name="任务"):
    """
    重试装饰器函数，最多重试max_retries次，每次重试前等待delay秒
    
    Args:
        func: 要执行的函数（无参数）
        max_retries: 最大重试次数
        delay: 重试间隔（秒）
        task_name: 任务名称，用于日志
    
    Returns:
        函数执行结果，如果所有重试都失败则返回None
    """
    for attempt in range(max_retries):
        try:
            result = func()
            # 如果函数返回False或None表示失败，需要重试
            if result is False or result is None:
                if attempt < max_retries - 1:
                    logger.warning(f"{task_name} 执行失败，{delay}秒后进行第 {attempt + 2} 次重试（共{max_retries}次）")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"{task_name} 执行失败，已达最大重试次数 {max_retries} 次")
                    return None
            return result
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"{task_name} 执行异常: {e}，{delay}秒后进行第 {attempt + 2} 次重试（共{max_retries}次）")
                time.sleep(delay)
                continue
            else:
                logger.error(f"{task_name} 执行异常，已达最大重试次数 {max_retries} 次: {e}")
                return None
    return None

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
            logger.info("没有待处理的用户，【每日任务】结束")
            return
            
        for user in user_list:
            try:
                client = None
                # 1. 尝试使用redis存的 Cookie
                if user['uid'] and str(user['uid']) != str(user['phone']):
                    client = auth.get_client_by_uid(user['uid'])
                
                # 2. 失败则登录（仅当 LOGIN_METHOD=api 时才会真正走接口）
                if not client:
                    client = auth.login(user['phone'], user['password'], task_key=user['task_key'])
                
                if client:
                    logger.info(f"正在处理用户 {user['uid']} 的每日任务")
                    task = TaskManager(client)
                    

                    
                    # 获取并执行音乐人签到任务（带重试）
                    def execute_musician_checkin():
                        nonlocal client, task
                        if LOGIN_METHOD == "playwright":
                            # 用浏览器打开音乐人后台并监听 cycle/list（规避 checkToken/风控 301）
                            profile_dir = PLAYWRIGHT_PROFILE_BASEDIR
                            if PLAYWRIGHT_PROFILE_PER_USER:
                                safe_phone = "".join([c for c in str(user.get("phone")) if c.isdigit()]) or str(user.get("phone"))
                                profile_dir = os.path.join(PLAYWRIGHT_PROFILE_BASEDIR, safe_phone)
                            musician_cycle_missions_res = task.get_musician_cycle_mission_by_playwright(
                                profile_dir,
                                phone=user.get("phone"),
                                password=user.get("password"),
                            )
                        else:
                            musician_cycle_missions_res = task.get_musician_cycle_mission()
                        # 遇到 301（未登录）时，触发自动登录并重试
                        if musician_cycle_missions_res.get('code') == 301:
                            logger.warning(f"用户 {user['uid']} 音乐人接口返回 301，尝试自动登录刷新 Cookie 后重试")
                            new_client = auth.login(user['phone'], user['password'], task_key=user.get('task_key'))
                            if new_client:
                                client = new_client
                                task = TaskManager(client)
                            return False
                        if musician_cycle_missions_res.get('code') == 200:
                            musician_cycle_missions_data = musician_cycle_missions_res.get('data', {})
                            musician_cycle_missions_list = musician_cycle_missions_data.get('list', [])
                            success_count = 0
                            has_checkin_mission = False
                            missing_params = False
                            
                            for mission in musician_cycle_missions_list:
                                description = mission.get('description')
                                if "签到" not in description:
                                    continue
                                
                                has_checkin_mission = True
                                logger.info(f"发现签到任务：{description}")
                                userMissionId = mission.get('userMissionId')
                                period = mission.get('period')
                                
                                if userMissionId and period:
                                    logger.info(f"{description}：userMissionId={userMissionId}, period={period}")
                                    reward_obtain_res = task.reward_obtain(userMissionId, period)
                                    logger.info(f"{description}结果：{json.dumps(reward_obtain_res, ensure_ascii=False)[:100]}")
                                    if reward_obtain_res.get('code') == 200:
                                        success_count += 1
                                else:
                                    logger.warning(f"任务 {description} 缺少必要参数：userMissionId={userMissionId}, period={period}\nmission={mission}")
                                    missing_params = True  # 标记有参数缺失，但不立即返回，继续处理其他任务
                            
                            # 如果找到了签到任务但参数缺失，返回False触发重试
                            if has_checkin_mission and missing_params:
                                return False
                            
                            # 如果至少有一个签到任务成功，返回True
                            if success_count > 0:
                                return True
                            
                            # 如果没有找到签到任务，也算成功（可能已经签到过了）
                            return True
                        else:
                            logger.error(f"获取音乐人循环任务失败：{json.dumps(musician_cycle_missions_res, ensure_ascii=False)[:100]}")
                            return False  # 返回False触发重试
                    
                    # 使用重试机制执行音乐人签到任务
                    retry_with_backoff(
                        execute_musician_checkin,
                        max_retries=3,
                        delay=2,
                        task_name=f"用户 {user['uid']} 的音乐人签到任务"
                    )

                    # 执行日常签到任务
                    daily_task_res = task.daily_task()
                    logger.info(f"日常签到任务结果：{json.dumps(daily_task_res, ensure_ascii=False)[:100]}")

                else:
                    logger.error(f"用户 {user.get('uid')} 登录失败，无法执行每日任务")
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
            logger.info("没有待处理的用户，【间隔任务】结束")
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
                    
                    skip_reason = ""
                    next_execution_time = "未知"
                    
                    if last_send_date_str:
                        try:
                            last_send_date = datetime.strptime(last_send_date_str, '%Y-%m-%d').date()
                            today = date.today()
                            now = datetime.now()
                            days_since_last_send = (today - last_send_date).days
                            
                            # 检查间隔天数是否满足
                            if days_since_last_send < EXECUTION_INTERVAL_DAYS:
                                # 间隔天数不足
                                days_remaining = EXECUTION_INTERVAL_DAYS - days_since_last_send
                                next_execution_date = today + timedelta(days=days_remaining)
                                skip_reason = f"距离上次执行不足 {EXECUTION_INTERVAL_DAYS} 天（已过 {days_since_last_send} 天）"
                            else:
                                # 间隔天数已满足，检查每月发送次数
                                current_year_month = today.strftime('%Y-%m')
                                monthly_sends = user_record.get('monthly_sends', {})
                                current_month_count = monthly_sends.get(current_year_month, 0)
                                
                                if current_month_count >= MAX_MONTHLY_SENDS:
                                    # 每月发送次数已达上限，显示下个月1号的时间
                                    year, month = map(int, current_year_month.split('-'))
                                    if month == 12:
                                        next_month_date = date(year + 1, 1, 1)
                                    else:
                                        next_month_date = date(year, month + 1, 1)
                                    next_execution_date = next_month_date
                                    skip_reason = f"本月已发送 {current_month_count} 次，已达每月上限 {MAX_MONTHLY_SENDS} 次"
                                else:
                                    # 间隔天数已满足，但今天执行时间已过
                                    # SEND_TIME已在config.py中验证过，直接使用
                                    send_hour, send_minute = map(int, SEND_TIME.split(':'))
                                    send_time_today = datetime.combine(today, datetime.min.time().replace(hour=send_hour, minute=send_minute))
                                    if now >= send_time_today:
                                        next_execution_date = today + timedelta(days=1)
                                        skip_reason = "间隔天数已满足，但今天执行时间已过"
                                    else:
                                        next_execution_date = today
                                        skip_reason = "间隔天数已满足，等待执行时间"
                            
                            next_execution_time = f"{next_execution_date.strftime('%Y-%m-%d')} {SEND_TIME}"
                        except Exception as e:
                            logger.error(f"计算预计下次执行时间时发生错误: {e}")
                            skip_reason = "计算时间时发生错误"
                    else:
                        skip_reason = "没有发送记录"
                        next_execution_time = "下次定时检查时"
                    
                    logger.info(f"用户 {user_uid} {skip_reason}，跳过本次发布动态任务，预计下次执行时间：{next_execution_time}")
                    continue
                
                client = None
                # 1. 尝试使用redis存的 Cookie
                if user['uid'] and str(user['uid']) != str(user['phone']):
                    client = auth.get_client_by_uid(user['uid'])
                
                # 2. 失败则登录（仅当 LOGIN_METHOD=api 时才会真正走接口）
                if not client:
                    client = auth.login(user['phone'], user['password'], task_key=user['task_key'])
                
                if client:
                    logger.info(f"正在处理用户 {user['uid']} 的发布动态任务")
                    task = TaskManager(client)
                    
                    # 发布动态任务（带重试）
                    share_res = None
                    def execute_share_song():
                        nonlocal client, task
                        nonlocal share_res
                        if LOGIN_METHOD == 'playwright':
                            # 用浏览器发布（避免 code=250 安全验证分享异常）
                            from playwright_handle.friend import share_note_and_delete

                            profile_dir = PLAYWRIGHT_PROFILE_BASEDIR
                            if PLAYWRIGHT_PROFILE_PER_USER:
                                safe_phone = "".join([c for c in str(user.get('phone')) if c.isdigit()]) or str(user.get('phone'))
                                profile_dir = os.path.join(PLAYWRIGHT_PROFILE_BASEDIR, safe_phone)

                            msg = f"{datetime.now().strftime('%Y年%m月%d日%H:%M:%S')}早上好"
                            # 将当前可用的 cookie 注入到浏览器；若仍未登录则用账号密码再走一次登录流程
                            ok = share_note_and_delete(
                                profile_dir,
                                msg,
                                search_keyword="你好",
                                cookie_str=client.get_cookie_str(),
                                phone=user.get("phone"),
                                password=user.get("password"),
                            )
                            share_res = {"code": 200} if ok else {"code": 250, "msg": "playwright share failed"}
                        else:
                            share_res = task.share_song()
                        # 遇到 301（未登录）时，触发自动登录并重试
                        if share_res.get('code') == 301:
                            logger.warning(f"用户 {user['uid']} 分享接口返回 301，尝试自动登录刷新 Cookie 后重试")
                            new_client = auth.login(user['phone'], user['password'], task_key=user.get('task_key'))
                            if new_client:
                                client = new_client
                                task = TaskManager(client)
                            return False
                        if share_res.get('code') == 200:
                            logger.info(f"发布动态成功：{json.dumps(share_res, ensure_ascii=False)[:100]}")
                            return True
                        else:
                            logger.warning(f"发布动态失败：{json.dumps(share_res, ensure_ascii=False)[:100]}")
                            return False  # 返回False触发重试
                    
                    # 使用重试机制执行发布动态任务
                    success = retry_with_backoff(
                        execute_share_song,
                        max_retries=3,
                        delay=3,
                        task_name=f"用户 {user['uid']} 的发布动态任务"
                    )
                    
                    if success and share_res and share_res.get('code') == 200:
                        # 更新最后发送记录
                        update_last_send_record(user_uid)

                        # playwright 分支内部已负责监听分享接口并删除动态，这里不再重复删除
                        if LOGIN_METHOD != 'playwright':
                            id_ = share_res.get('event', {}).get('id')
                            if id_:
                                logger.info("等待 10 秒后删除动态")
                                time.sleep(10)
                                delete_res = task.delete_dynamic(id_)
                                logger.info(f'删除动态结果: {delete_res}')
                            else:
                                logger.warning("删除动态失败：动态ID获取失败")
                    elif not success:
                        logger.error(f"用户 {user['uid']} 发布动态任务重试3次后仍然失败")
                else:
                    logger.error(f"用户 {user['uid']} 登录失败，跳过发布动态任务")
            except Exception as e:
                logger.error(f"处理用户 {user.get('uid')} 的发布动态任务时发生异常: {e}")
                continue
                
    except Exception as e:
        logger.error(f"间隔任务执行异常: {e}")
    
    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 间隔任务执行完毕")


def main():
    """主函数"""
    logger.info("网易音乐人任务调度器启动")
    
    # 从配置文件导入的SEND_TIME已经验证过，直接使用
    hour, minute = map(int, SEND_TIME.split(':'))
    
    # 创建调度器
    scheduler = BlockingScheduler(timezone='Asia/Shanghai')
    
    # 计算间隔任务的执行时间（每日任务时间 + 5分钟）
    interval_minute = minute + 5
    interval_hour = hour
    if interval_minute >= 60:
        interval_minute -= 60
        interval_hour += 1
        if interval_hour >= 24:
            interval_hour -= 24
    
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
            trigger=CronTrigger(hour=interval_hour, minute=interval_minute, day_of_week='*'),  # 间隔5分钟执行，避免冲突
            id='netease_interval_task',
            name='网易音乐人发布动态任务',
            replace_existing=True
        )
        
        logger.info(f"每日任务已添加，每天 {SEND_TIME} 执行")
        logger.info(f"间隔任务已添加，每天 {interval_hour:02d}:{interval_minute:02d} 执行检查，实际执行间隔：每 {EXECUTION_INTERVAL_DAYS} 天")
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
