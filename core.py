import base64
import binascii
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import random
import time
import urllib.parse

import redis
import requests
from Crypto.Cipher import AES

# --- 配置部分 ---

import os

# 确保log目录存在
os.makedirs('log', exist_ok=True)

# 配置日志
logger = logging.getLogger('netease_music')
logger.setLevel(logging.INFO)

if not logger.handlers:
    # 创建格式化器
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    
    # 创建文件处理器 - 带轮转功能
    file_handler = logging.handlers.RotatingFileHandler(
        'log/netease_music.log',
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

# 从配置文件导入Redis配置
from config import REDIS_POOL, REDIS_CONF, LOGIN_METHOD, PLAYWRIGHT_PROFILE_BASEDIR, PLAYWRIGHT_PROFILE_PER_USER


# --- 1. 基础加解密工具类 ---
class CryptoUtil:
    AES_IV = b'0102030405060708'

    @staticmethod
    def aes_encrypt(text, key):
        try:
            if isinstance(text, str): 
                text = text.encode('utf-8')
            pad = 16 - len(text) % 16
            text = text + pad * chr(pad).encode('utf-8')
            cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, CryptoUtil.AES_IV)
            return base64.b64encode(cipher.encrypt(text)).decode('utf-8')
        except Exception as e:
            logger.error(f"AES加密失败: {e}")
            raise

    @staticmethod
    def rsa_encrypt(text, pubKey, modulus):
        try:
            text = text[::-1]
            rs = pow(int(binascii.hexlify(text.encode('utf-8')), 16), int(pubKey, 16), int(modulus, 16))
            return format(rs, 'x').zfill(256)
        except Exception as e:
            logger.error(f"RSA加密失败: {e}")
            raise

    @staticmethod
    def create_secret_key(size=16):
        return ''.join([hex(random.randint(0, 15))[2:] for _ in range(size)])

    @staticmethod
    def md5(text):
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    @staticmethod
    def dy2x(fR8J, fC8u):
        """
        对应JavaScript中的j7c.Dy2x函数
        生成指定范围内的随机整数
        """
        return int(random.uniform(fR8J, fC8u))

    @staticmethod
    def oi0x(bu7n):
        """
        对应JavaScript中的j7c.oI0x函数
        生成指定位数的随机数字字符串
        """
        bu7n = max(0, min(bu7n or 8, 30))
        fR8J = 10 ** (bu7n - 1)
        fC8u = fR8J * 10
        return str(CryptoUtil.dy2x(fR8J, fC8u))

    @staticmethod
    def generate_check_token():
        try:
            import execjs
            with open('./checkToken.js', 'r', encoding='utf-8') as f:
                tst = f.read()
            checkToken = execjs.compile(tst).call('get_token')
            return checkToken
        except FileNotFoundError:
            logger.error("checkToken.js 文件不存在")
            return ""
        except ImportError:
            logger.error("未安装execjs模块")
            return ""
        except Exception as e:
            logger.error(f"生成 checkToken 失败: {e}")
            return ""
    
    @staticmethod
    def generate_publish_uuid():
        """
        生成发布动态的UUID，对应JavaScript中的 "publish-" + +(new Date) + j7c.oI0x(5)
        """
        timestamp = int(time.time() * 1000)  # 对应JavaScript中的+(new Date)
        random_num_str = CryptoUtil.oi0x(5)  # 调用oi0x函数生成5位随机数字字符串
        return f"publish-{timestamp}{random_num_str}"
    
    @staticmethod
    def generate_csrf_token():
        """
        生成或提取CSRF令牌
        """
        return hashlib.md5(str(random.random()).encode()).hexdigest()


# --- 2. 网易云特定加密参数生成类 ---
class NeteaseSecurity:
    MODULUS = '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7'
    NONCE = '0CoJUm6Qyw8W8jud'
    PUBKEY = '010001'

    @classmethod
    def encrypt_weapi(cls, data_dict):
        try:
            text = json.dumps(data_dict)
            secret_key = CryptoUtil.create_secret_key(16)
            params = CryptoUtil.aes_encrypt(text, cls.NONCE)
            params = CryptoUtil.aes_encrypt(params, secret_key)
            enc_sec_key = CryptoUtil.rsa_encrypt(secret_key, cls.PUBKEY, cls.MODULUS)
            return {'params': params, 'encSecKey': enc_sec_key}
        except Exception as e:
            logger.error(f"加密API参数失败: {e}")
            raise


# --- 3. 网易云 API 客户端类 ---
class NeteaseClient:
    BASE_URL = 'https://music.163.com'
    RETRY_TIMES = 3
    RETRY_DELAY = 2

    def __init__(self, cookie_str=None, uid=None):
        self.session = requests.Session()
        self.uid = uid

        # 通用 Header
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.30 Safari/537.36',
            'Referer': 'https://music.163.com/',
            'Accept': '*/*'
        })

        if cookie_str:
            self._parse_and_set_cookie(cookie_str)

    def _parse_and_set_cookie(self, cookie_str):
        """将浏览器复制的 key=val; key2=val2 字符串解析进 Session"""
        try:
            cookie_dict = {}
            for item in cookie_str.split(';'):
                item = item.strip()
                if '=' in item:
                    k, v = item.split('=', 1)
                    cookie_dict[k] = v
            if cookie_dict:
                self.session.cookies.update(cookie_dict)
            else:
                logger.warning("Cookie解析结果为空")
        except Exception as e:
            logger.error(f"Cookie 解析失败: {e}")

    def get_cookie_str(self):
        """将当前 Session 的 Cookie 导出为字符串，方便存 Redis"""
        try:
            cookie_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
            return '; '.join([f"{k}={v}" for k, v in cookie_dict.items()])
        except Exception as e:
            logger.error(f"导出Cookie字符串失败: {e}")
            return ''

    def request(self, method, path, data=None, encrypt=True):
        url = self.BASE_URL + path
        payload = None
        
        for retry in range(self.RETRY_TIMES):
            try:
                if method.upper() == 'POST' and data:
                    payload = NeteaseSecurity.encrypt_weapi(data) if encrypt else data

                resp = self.session.request(method, url, data=payload, timeout=10)
                resp.encoding = 'utf-8'
                
                if resp.status_code != 200:
                    logger.warning(f"请求返回非200状态码: {resp.status_code}, URL: {url}")
                    if retry >= self.RETRY_TIMES - 1:
                        return {'code': resp.status_code, 'msg': f'HTTP错误: {resp.status_code}'}
                    time.sleep(self.RETRY_DELAY)
                    continue
                
                try:
                    return resp.json()
                except json.JSONDecodeError:
                    # 出现这个错误通常是 403 或者被拦截返回了 HTML
                    logger.error(f"非 JSON 响应 [Code: {resp.status_code}]: {resp.text[:50]}")
                    if retry >= self.RETRY_TIMES - 1:
                        return {'code': -1, 'msg': '非 JSON 响应'}
                    time.sleep(self.RETRY_DELAY)
                    continue

            except requests.RequestException as e:
                logger.error(f"网络请求异常 [{path}]: {e}")
                if retry >= self.RETRY_TIMES - 1:
                    return {'code': 500, 'msg': str(e)}
                time.sleep(self.RETRY_DELAY)
                continue
        
        return {'code': 500, 'msg': '请求失败，已达最大重试次数'}

    @property
    def csrf_token(self):
        csrf = self.session.cookies.get('__csrf')
        if csrf: 
            return csrf
        logger.warning("Cookie中未找到__csrf，生成新的csrf_token")
        return CryptoUtil.generate_csrf_token()


# --- 4. 账号与登录管理类 ---
class AuthManager:
    def __init__(self):
        try:
            self.redis = redis.Redis(connection_pool=REDIS_POOL) if REDIS_POOL else None
            if self.redis:
                self.redis.ping()
            else:
                logger.error("Redis连接池未初始化")
        except Exception as e:
            logger.error(f"初始化Redis连接失败: {e}")
            self.redis = None

    def _get_uid_by_cookie(self, cookie_str: str):
        """
        使用浏览器 Cookie 调用一系列账号信息接口，提取 uid。
        """
        client = NeteaseClient(cookie_str=cookie_str)
        candidates = [
            ("GET", "/api/nuser/account/get", False, None),
            ("GET", "/api/w/nuser/account/get", False, None),
            ("GET", "/api/v1/user/info", False, None),
            ("POST", "/weapi/w/nuser/account/get", True, {}),
        ]
        for method, path, encrypt, data in candidates:
            try:
                res = client.request(method, path, data=data, encrypt=encrypt)
            except Exception as e:
                logger.warning(f"通过 Cookie 获取 uid 失败：{method} {path} - {e}")
                continue
            if not isinstance(res, dict):
                continue
            uid = None
            account = res.get("account") or {}
            profile = res.get("profile") or {}
            if isinstance(account, dict):
                uid = account.get("id") or uid
            if isinstance(profile, dict):
                uid = profile.get("userId") or uid
            if uid:
                return int(uid)
        return None

    def _login_via_api(self, phone, password, task_key=None):
        client = NeteaseClient()
        pw_md5 = CryptoUtil.md5(password)
        data = {'phone': phone, 'password': pw_md5, 'rememberLogin': 'true'}

        logger.info(f"正在登录用户: {phone}")
        res = client.request('POST', '/weapi/login/cellphone', data)

        if res.get('code') == 200 and res.get('account'):
            real_uid = res['account']['id']
            client.uid = real_uid

            # 保存为字符串
            if not self._save_session(real_uid, client.get_cookie_str(), res):
                logger.warning(f"用户 {real_uid} 登录成功但保存会话失败")

            # 回写真实 UID 逻辑
            if task_key and self.redis:
                try:
                    user_info_str = self.redis.hget('netease:music:task', task_key)
                    if user_info_str:
                        user_info = json.loads(user_info_str)
                        if str(user_info.get('uid')) != str(real_uid):
                            user_info['uid'] = real_uid
                            self.redis.hset('netease:music:task', task_key, json.dumps(user_info))
                            logger.info(f"绑定真实 UID: {real_uid}")
                except Exception as e:
                    logger.error(f"回写 UID 失败: {e}")

            logger.info(f"用户 {real_uid} 登录成功")
            return client
        else:
            logger.error(f"登录失败: {res.get('msg', res)}")
            return None

    def _login_via_playwright(self, phone, password, task_key=None):
        """
        使用 Playwright 浏览器完成登录，并把 Cookie 写入 Redis，返回 NeteaseClient。
        """
        try:
            from playwright_handle.login import browser_login  # 延迟导入，避免循环
        except ImportError as e:
            logger.error(f"导入 Playwright 登录模块失败: {e}")
            return None

        profile_dir = PLAYWRIGHT_PROFILE_BASEDIR
        if PLAYWRIGHT_PROFILE_PER_USER:
            # phone 可能包含 +86 等符号，简单做下目录安全化
            safe_phone = "".join([c for c in str(phone) if c.isdigit()]) or str(phone)
            profile_dir = os.path.join(PLAYWRIGHT_PROFILE_BASEDIR, safe_phone)

        logger.info(f"使用 Playwright 为账号 {phone} 执行登录（profile={profile_dir}）...")
        try:
            cookie_str = browser_login(phone, password, profile_dir=profile_dir)
        except Exception as e:
            logger.error(f"Playwright 登录失败: {e}")
            return None

        if not cookie_str:
            logger.error("Playwright 登录未获取到有效 Cookie")
            return None

        uid = self._get_uid_by_cookie(cookie_str)
        if not uid:
            logger.error("使用 Cookie 无法识别 uid，登录失败")
            return None

        client = NeteaseClient(cookie_str=cookie_str, uid=uid)

        # 保存到 Redis
        if not self._save_session(uid, cookie_str, {"uid": uid}):
            logger.warning(f"用户 {uid} 登录成功但保存会话失败")

        # 回写真实 UID
        if task_key and self.redis:
            try:
                user_info_str = self.redis.hget('netease:music:task', task_key)
                if user_info_str:
                    user_info = json.loads(user_info_str)
                    if str(user_info.get('uid')) != str(uid):
                        user_info['uid'] = uid
                        self.redis.hset('netease:music:task', task_key, json.dumps(user_info))
                        logger.info(f"绑定真实 UID: {uid}")
            except Exception as e:
                logger.error(f"回写 UID 失败: {e}")

        logger.info(f"用户 {uid} 通过 Playwright 登录成功")
        return client

    def login(self, phone, password, task_key=None):
        if LOGIN_METHOD == 'playwright':
            return self._login_via_playwright(phone, password, task_key)
        # 默认走 API 登录
        return self._login_via_api(phone, password, task_key)

    def get_client_by_uid(self, uid):
        if not uid or not self.redis:
            return None
        
        try:
            # 读取字符串 Cookie
            cookie_str = self.redis.get(f'netease:music:user:{uid}:cookie')
            if cookie_str:
                client = NeteaseClient(cookie_str=cookie_str, uid=uid)

                logger.info(f"正在检查用户 {uid} 的 Cookie 有效性...")

                check = client.request('GET', f'/api/v1/user/detail/{uid}', encrypt=False)

                # 只要 code 是 200 且能拿到 profile，就认为有效
                if check.get('code') == 200 and check.get('profile'):
                    logger.info(f"用户 {uid} Cookie 有效 (昵称: {check['profile'].get('nickname', '未知')})")
                    return client
                else:
                    # 如果返回的不是 200，记录一下返回了啥，方便调试
                    logger.warning(f"用户 {uid} Cookie 可能已失效，状态码: {check.get('code')}")
                    # 删除失效的Cookie
                    try:
                        self.redis.delete(f'netease:music:user:{uid}:cookie')
                        self.redis.delete(f'netease:music:user:{uid}:userdata')
                        logger.info(f"已删除用户 {uid} 的失效Cookie")
                    except Exception as e:
                        logger.error(f"删除失效Cookie失败: {e}")
                    return None

        except json.JSONDecodeError as e:
            logger.error(f"解析用户 {uid} 数据时发生JSON错误: {e}")
        except Exception as e:
            logger.warning(f"检测用户 {uid} 时发生异常: {e}")

        return None

    def get_all_users_credentials(self):
        if not self.redis:
            logger.error("Redis连接不可用，无法获取用户凭证")
            return []
            
        try:
            users = self.redis.hgetall('netease:music:task')
            user_list = []
            for task_key, info_str in users.items():
                try:
                    info = json.loads(info_str)
                    if all(key in info for key in ['phone', 'password']):
                        user_list.append({
                            'task_key': task_key,
                            'uid': info.get('uid', task_key),  # 优先取 uid
                            'phone': info.get('phone'),
                            'password': info.get('password')
                        })
                    else:
                        logger.warning(f"用户数据不完整，缺少必要字段: {task_key}")
                except json.JSONDecodeError:
                    logger.error(f"解析用户数据失败: {task_key}")
                except Exception as e:
                    logger.error(f"处理用户数据时发生异常: {e}")
            return user_list
        except Exception as e:
            logger.error(f"获取用户凭证时发生异常: {e}")
            return []

    def _save_session(self, uid, cookie_str, user_data):
        if not self.redis or not cookie_str:
            return False
            
        try:
            # Key 改回简单的 :cookie，存纯字符串
            self.redis.set(f'netease:music:user:{uid}:cookie', cookie_str, ex=86400 * 7)  # 7天过期
            self.redis.set(f'netease:music:user:{uid}:userdata', json.dumps(user_data), ex=86400 * 7)
            return True
        except Exception as e:
            logger.error(f"保存用户 {uid} 会话失败: {e}")
            return False


# --- 5. 任务执行类 ---
class TaskManager:
    def __init__(self, client: NeteaseClient):
        self.client = client

    # 网易云日常签到任务
    def daily_task(self):
        """网易云音乐签到任务"""
        data = {
            "type": 1 # 0为安卓端签到3点经验,1为网页签到2点经验
        }
        return self.client.request(
            'POST', 
            f'/weapi/point/dailyTask', 
            data=data
        )
    # 获取音乐人任务列表
    def get_musician_cycle_mission(self,actionType="102",platform="200"):
        """获取音乐人任务列表"""
        csrf = self.client.csrf_token
        check_token = CryptoUtil.generate_check_token()
        data = {
            "actionType": actionType,  # 102
            "platform": platform,  # 200
            "csrf_token": csrf,  # 不传也行
        }
        # 注意：音乐人接口对 checkToken 更敏感；生成失败时传空字符串可能会触发 301/风控
        if check_token:
            data["checkToken"] = check_token
        else:
            logger.warning(
                "checkToken 生成失败（缺少 JS 运行时/Node.js 或 execjs 不可用），将不携带 checkToken 请求音乐人接口；"
                "若仍返回 301，请安装 Node.js 并确保在 PATH 中可执行 `node`。"
            )
        return self.client.request(
            'POST', 
            f'/weapi/nmusician/workbench/mission/cycle/list?csrf_token={csrf}',
            data=data
        )

    # 领取音乐人云豆签到任务
    def reward_obtain(self, userMissionId, period):
        """领取音乐人云豆签到任务"""
        params = {
            "userMissionId": userMissionId,
            "period": period,
        }
        return self.client.request(
            'POST', 
            f'/weapi/nmusician/workbench/mission/reward/obtain/new', 
            data=params
        )
    
    # 获取随机歌曲
    def get_random_song(self):
        try:
            res = requests.get(
                "https://music.163.com/api/v6/playlist/detail?id=3778678&n=100",
                headers={'User-Agent': self.client.session.headers['User-Agent']},
                timeout=5
            ).json()
            tracks = res['playlist']['tracks']
            song = random.choice(tracks)
            return str(song['id'])
        except:
            return "2123990711"

    # 创建分享音乐动态
    def share_song(self):
        song_id = self.get_random_song()
        msg = f"{time.strftime('%Y年%m月%d日%H:%M:%S')}早上好"

        # check_token = ""  # 省略 checkToken 读取逻辑

        check_token = CryptoUtil.generate_check_token()
        uuid = CryptoUtil.generate_publish_uuid()

        # 确保 csrf_token 存在
        csrf = self.client.csrf_token
        if not csrf:
            logger.warning("未找到 CSRF Token，尝试使用默认值")
        params = {
            "id": song_id,
            "type": "song",
            "msg": msg,
            "uuid": uuid, # 不传也行
            "csrf_token": csrf # 不传也行
        }
        if check_token:
            params["checkToken"] = check_token
        else:
            logger.warning(
                "checkToken 生成失败（缺少 JS 运行时/Node.js 或 execjs 不可用），将不携带 checkToken 进行分享请求；"
                "若分享接口返回 301，请安装 Node.js 并确保在 PATH 中可执行 `node`。"
            )
        return self.client.request('POST', f'/weapi/share/friends/resource?csrf_token={csrf}', params)

    # 删除动态
    def delete_dynamic(self, event_id):
        csrf = self.client.csrf_token
        params = {
            'id': str(event_id),
            # 'csrf_token': csrf # 不传也行
        }
        return self.client.request('POST', f'/weapi/event/delete?csrf_token={csrf}', params)


# --- Main ---
if __name__ == '__main__':
    auth = AuthManager()
    user_list = auth.get_all_users_credentials()
    logger.info(f"发现 {len(user_list)} 个待处理用户")

    for user in user_list:
        try:
            client = None
            # 1. 尝试使用redis存的 Cookie
            if user['uid'] and str(user['uid']) != str(user['phone']):
                client = auth.get_client_by_uid(user['uid'])
            else:
                logger.info(f"用户 {user.get('phone')} 没有有效的UID，将直接登录")

            # 2. 失败则登录（仅当 LOGIN_METHOD=api 时才会真正走接口）
            if not client:
                client = auth.login(user['phone'], user['password'], task_key=user['task_key'])

            if client:
                logger.info(f"正在处理用户 {user['uid']}")
                task = TaskManager(client)

                musician_cycle_missions_res = task.get_musician_cycle_mission()
                if musician_cycle_missions_res.get('code') == 301:
                    logger.warning(f"用户 {user['uid']} 音乐人接口返回 301，触发自动登录后重试一次")
                    client = auth.login(user['phone'], user['password'], task_key=user.get('task_key'))
                    if client:
                        task = TaskManager(client)
                        musician_cycle_missions_res = task.get_musician_cycle_mission()
                if musician_cycle_missions_res.get('code') == 200:
                    musician_cycle_missions_data = musician_cycle_missions_res.get('data', {})
                    musician_cycle_missions_list = musician_cycle_missions_data.get('list', [])
                    for mission in musician_cycle_missions_list:
                        description = mission.get('description')
                        if "签到" in description:
                            logger.info(f"发现签到任务：{description}")
                            userMissionId = mission.get('userMissionId')
                            period = mission.get('period')
                            if userMissionId and period:
                                logger.info(f"{description}：userMissionId={userMissionId}, period={period}")
                                reward_obtain_res = task.reward_obtain(userMissionId, period)
                                logger.info(f"{description}结果：{json.dumps(reward_obtain_res, ensure_ascii=False)[:100]}")
                            else:
                                logger.error(f"执行任务 {description} 失败：mission={mission}")
                    if not musician_cycle_missions_list:
                        logger.info("未找到任何音乐人任务")
                else:
                    logger.error(f"获取音乐人循环任务失败：{json.dumps(musician_cycle_missions_res, ensure_ascii=False)[:100]}")

                logger.info(f"开始音乐人发布动态任务：")
                share_res = task.share_song()
                if share_res.get('code') == 301:
                    logger.warning(f"用户 {user['uid']} 分享接口返回 301，触发自动登录后重试一次")
                    client = auth.login(user['phone'], user['password'], task_key=user.get('task_key'))
                    if client:
                        task = TaskManager(client)
                        share_res = task.share_song()
                if share_res.get('code') == 200:
                    logger.info(f"发布动态成功：{json.dumps(share_res, ensure_ascii=False)[:100]}")
                    id_ = share_res.get('event', {}).get('id')
                    if id_:
                        logger.info("等待 10 秒后删除动态")
                        time.sleep(10)
                        delete_res = task.delete_dynamic(id_)
                        logger.info(f'删除动态结果: {delete_res}')
                    else:
                        logger.warning("删除动态失败：动态ID获取失败")
                else:
                    logger.warning(f"发布动态失败：{json.dumps(share_res, ensure_ascii=False)[:100]}")

                daily_task_res = task.daily_task()
                if daily_task_res.get('code') == 301:
                    logger.warning(f"用户 {user['uid']} 日常签到接口返回 301，触发自动登录后重试一次")
                    client = auth.login(user['phone'], user['password'], task_key=user.get('task_key'))
                    if client:
                        task = TaskManager(client)
                        daily_task_res = task.daily_task()
                logger.info(f"日常签到任务结果：{json.dumps(daily_task_res, ensure_ascii=False)[:100]}")
            else:
                logger.error(f"用户 {user.get('phone')} 无法获取有效的客户端实例")
        except Exception as e:
            logger.error(f"处理用户时发生异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            continue