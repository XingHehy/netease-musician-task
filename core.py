import base64
import binascii
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import random
import time

import redis
import requests
from Crypto.Cipher import AES

# --- 配置部分 ---

import os

# 确保log目录存在
os.makedirs('log', exist_ok=True)

# 配置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

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


# Redis配置 - 支持环境变量
REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/5')

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
    
    # 测试连接
    redis_conn = redis.Redis(**REDIS_CONF)
    redis_conn.ping()
    logger.info(f"成功连接到Redis: {REDIS_URL}")
except Exception as e:
    logger.error(f"Redis连接失败: {e}")
    # 使用默认本地配置
    REDIS_CONF = {
        'host': 'localhost',
        'port': 6379,
        'db': 0,
        'password': None,
        'decode_responses': True
    }


# --- 1. 基础加解密工具类 (保持不变) ---
class CryptoUtil:
    AES_IV = b'0102030405060708'

    @staticmethod
    def aes_encrypt(text, key):
        if isinstance(text, str): text = text.encode('utf-8')
        pad = 16 - len(text) % 16
        text = text + pad * chr(pad).encode('utf-8')
        cipher = AES.new(key.encode('utf-8'), AES.MODE_CBC, CryptoUtil.AES_IV)
        return base64.b64encode(cipher.encrypt(text)).decode('utf-8')

    @staticmethod
    def rsa_encrypt(text, pubKey, modulus):
        text = text[::-1]
        rs = pow(int(binascii.hexlify(text.encode('utf-8')), 16), int(pubKey, 16), int(modulus, 16))
        return format(rs, 'x').zfill(256)

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
        """
        生成checkToken
        """
        import execjs
        with open('./checkToken.js', 'r', encoding='utf-8') as f:
            tst = f.read()
        checkToken = execjs.compile(tst).call('get_token')
        return checkToken

    @staticmethod
    def generate_publish_uuid():
        """
        生成发布动态的UUID，对应JavaScript中的 "publish-" + +(new Date) + j7c.oI0x(5)
        """
        timestamp = int(time.time() * 1000)  # 对应JavaScript中的+(new Date)
        random_num_str = CryptoUtil.oi0x(5)  # 调用oi0x函数生成5位随机数字字符串
        return f"publish-{timestamp}{random_num_str}"


# --- 2. 网易云特定加密参数生成类 (保持不变) ---
class NeteaseSecurity:
    MODULUS = '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7'
    NONCE = '0CoJUm6Qyw8W8jud'
    PUBKEY = '010001'

    @classmethod
    def encrypt_weapi(cls, data_dict):
        text = json.dumps(data_dict)
        secret_key = CryptoUtil.create_secret_key(16)
        params = CryptoUtil.aes_encrypt(text, cls.NONCE)
        params = CryptoUtil.aes_encrypt(params, secret_key)
        enc_sec_key = CryptoUtil.rsa_encrypt(secret_key, cls.PUBKEY, cls.MODULUS)
        return {'params': params, 'encSecKey': enc_sec_key}


# --- 3. 网易云 API 客户端类 (支持 Cookie 字符串) ---
class NeteaseClient:
    BASE_URL = 'https://music.163.com'

    def __init__(self, cookie_str=None, uid=None):
        self.session = requests.Session()
        self.uid = uid

        # 通用 Header
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.30 Safari/537.36',
            'Referer': 'https://music.163.com/',
            'Accept': '*/*'
        })

        # 【修改点】解析字符串 Cookie 并注入 Session
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
            self.session.cookies.update(cookie_dict)
        except Exception as e:
            logger.error(f"Cookie 解析失败: {e}")

    def get_cookie_str(self):
        """【修改点】将当前 Session 的 Cookie 导出为字符串，方便存 Redis"""
        cookie_dict = requests.utils.dict_from_cookiejar(self.session.cookies)
        return '; '.join([f"{k}={v}" for k, v in cookie_dict.items()])

    def request(self, method, path, data=None, encrypt=True):
        url = self.BASE_URL + path
        payload = None
        try:
            if method.upper() == 'POST' and data:
                payload = NeteaseSecurity.encrypt_weapi(data) if encrypt else data

            resp = self.session.request(method, url, data=payload, timeout=10)
            resp.encoding = 'utf-8'
            try:
                return resp.json()
            except json.JSONDecodeError:
                # 出现这个错误通常是 403 或者被拦截返回了 HTML
                logger.error(f"非 JSON 响应 [Code: {resp.status_code}]: {resp.text[:50]}")
                return {'code': -1, 'msg': '非 JSON 响应'}

        except requests.RequestException as e:
            logger.error(f"网络请求异常 [{path}]: {e}")
            return {'code': 500, 'msg': str(e)}

    @property
    def csrf_token(self):
        return self.session.cookies.get('__csrf', '')


# --- 4. 账号与登录管理类 (Redis 存取字符串) ---
class AuthManager:
    def __init__(self):
        self.redis = redis.Redis(**REDIS_CONF)

    def login(self, phone, password, task_key=None):
        client = NeteaseClient()
        pw_md5 = CryptoUtil.md5(password)
        data = {'phone': phone, 'password': pw_md5, 'rememberLogin': 'true'}

        logger.info(f"正在登录用户: {phone}")
        res = client.request('POST', '/weapi/login/cellphone', data)

        if res.get('code') == 200:
            real_uid = res['account']['id']
            client.uid = real_uid

            # 【修改点】保存为字符串
            self._save_session(real_uid, client.get_cookie_str(), res)

            # 回写真实 UID 逻辑 (保持不变)
            if task_key:
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

    def get_client_by_uid(self, uid):
        if not uid: return None
        try:
            # 读取字符串 Cookie
            cookie_str = self.redis.get(f'netease:music:user:{uid}:cookie')
            if cookie_str:
                client = NeteaseClient(cookie_str=cookie_str, uid=uid)

                # 【修改核心】换用更温和的 GET 接口检测，不加密 (encrypt=False)
                # 访问用户详情页，如果 Cookie 有效，会返回 200 和用户数据
                # 如果 Cookie 失效，通常会返回 403 或 301
                logger.info(f"正在检查用户 {uid} 的 Cookie 有效性...")

                # 注意：这里使用 request('GET', ..., encrypt=False)
                check = client.request('GET', f'/api/v1/user/detail/{uid}', encrypt=False)

                # 只要 code 是 200 且能拿到 profile，就认为有效
                if check.get('code') == 200 and check.get('profile'):
                    logger.info(f"用户 {uid} Cookie 有效 (昵称: {check['profile'].get('nickname')})")
                    return client
                else:
                    # 如果返回的不是 200，记录一下返回了啥，方便调试
                    logger.warning(f"用户 {uid} Cookie 可能已失效，状态码: {check.get('code')}")
                    # 只有明确失败才返回 None
                    return None

        except Exception as e:
            # 如果解析 JSON 报错，说明可能返回了 HTML，那就是 Cookie 真的彻底挂了或者被强力拦截
            logger.warning(f"检测用户 {uid} 时发生异常 (通常意味着 Cookie 失效): {e}")

        return None

    def get_all_users_credentials(self):
        users = self.redis.hgetall('netease:music:task')
        user_list = []
        for task_key, info_str in users.items():
            try:
                info = json.loads(info_str)
                user_list.append({
                    'task_key': task_key,
                    'uid': info.get('uid', task_key),  # 优先取 uid
                    'phone': info.get('phone'),
                    'password': info.get('password')
                })
            except:
                continue
        return user_list

    def _save_session(self, uid, cookie_str, user_data):
        # 【修改点】Key 改回简单的 :cookie，存纯字符串
        self.redis.set(f'netease:music:user:{uid}:cookie', cookie_str)
        self.redis.set(f'netease:music:user:{uid}:userdata', json.dumps(user_data))


# --- 5. 任务执行类 (保持不变) ---
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
    def get_musician_cycle_mission(self):
        """获取音乐人任务列表"""
        data = {
            "actionType": 102,
            "platform": 200
        }
        return self.client.request(
            'POST', 
            f'/weapi/nmusician/workbench/mission/cycle/list', 
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
        #
        # check_token = CryptoUtil.generate_check_token()
        # uuid = CryptoUtil.generate_publish_uuid()

        # 确保 csrf_token 存在
        csrf = self.client.csrf_token
        if not csrf:
            logger.warning("未找到 CSRF Token，尝试使用默认值")

        params = {
            "id": song_id,
            "type": "song",
            "msg": msg,
            # "checkToken": check_token, # 要么传真的，要么不传或传空字符串
            # "uuid": uuid, # 不传也行
            # "csrf_token": csrf # 不传也行
        }
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

            # 2. 失败则登录
            if not client:
                client = auth.login(user['phone'], user['password'], task_key=user['task_key'])

            if client:
                logger.info(f"正在处理用户 {user['uid']}")
                task = TaskManager(client)

                daily_task_res = task.daily_task()
                logger.info(f"日常签到任务结果：{json.dumps(daily_task_res, ensure_ascii=False)[:100]}")
                
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

                logger.info(f"开始音乐人发布动态任务：")
                share_res = task.share_song()
                if share_res.get('code') == 200:
                    logger.info(f"分享成功：{json.dumps(share_res, ensure_ascii=False)[:100]}")
                    event_id = share_res.get('event', {}).get('id')
                    if event_id:
                        logger.info("等待 10 秒后删除动态")
                        time.sleep(10)
                        delete_res = task.delete_dynamic(event_id)
                        logger.info(f'删除动态结果: {delete_res}')
                else:
                    logger.warning(f"分享失败：{json.dumps(share_res, ensure_ascii=False)[:100]}")
        except Exception as e:
            logger.error(f"异常: {e}")