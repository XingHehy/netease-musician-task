import base64
import binascii
import json
from Crypto.Cipher import AES

# 全局常量（与加密函数一致）
modulus = '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7'
nonce = '0CoJUm6Qyw8W8jud'
pubKey = '010001'


def aesDecrypt(text, secKey):
    """
    AES解密函数（对应aesEncrypt）
    """
    if isinstance(text, str):
        text = text.encode('utf-8')
    
    # Base64解码
    text = base64.b64decode(text)
    
    iv = '0102030405060708'.encode('utf-8')
    
    # AES解密（CBC模式）
    cipher = AES.new(secKey.encode('utf-8'), AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(text)
    
    # 去除PKCS#7填充
    padding_len = decrypted[-1]
    decrypted = decrypted[:-padding_len]
    
    return decrypted.decode('utf-8')


def rsaDecrypt(text, priKey, modulus):
    """
    RSA解密函数（对应rsaEncrypt）
    注意：需要私钥才能解密
    """
    # RSA解密需要私钥，这里仅提供框架
    # 在实际应用中，由于没有私钥，通常无法直接解密encSecKey
    raise NotImplementedError("RSA解密需要私钥，无法直接解密")


def decrypt_params(params, secKey):
    """
    解密params
    """
    # 第一次解密：用secKey（对应第二次加密）
    decrypted_once = aesDecrypt(params, secKey)
    # 第二次解密：用nonce（对应第一次加密）
    decrypted_twice = aesDecrypt(decrypted_once, nonce)
    return decrypted_twice


def decrypt_netease_params(params, encSecKey=None, secKey=None):
    """
    解密网易云音乐的加密参数
    
    参数:
        params: 加密后的params字符串
        encSecKey: 加密后的secKey字符串（可选）
        secKey: 原始secKey（可选，优先使用）
    
    返回:
        decrypted_params: 解密后的参数（JSON格式）
    """
    if not secKey:
        # 没有私钥时，需要手动提供secKey
        raise ValueError("请提供原始secKey参数")
    
    # 解密params
    decrypted_json = decrypt_params(params, secKey)
    return json.loads(decrypted_json)


def decrypt_with_known_seckey(params, secKey):
    """
    使用已知的secKey解密params
    
    参数:
        params: 加密后的params字符串
        secKey: 16位的secKey字符串
    
    返回:
        decrypted_params: 解密后的参数（JSON格式）
    """
    decrypted_json = decrypt_params(params, secKey)
    return json.loads(decrypted_json)


# 示例用法（如果直接运行此文件）
if __name__ == "__main__":
    # secKey = "vtLXqdjWWCqxSfJ0"
#     data = {
#     "encText": "uaxalzdKH/iBaDAI9PN7MpAsPOgf7cGLfwkMCDrsMk9d+FH6UW363aKQx6SL6+0Ve7janetTYNCMdXalCl4OITawxU9mxojcIDMAAHR3hQUl3sfoYkRERMNR0HckoC8d81YNpLJHc3ZPUOhztSxF5lh2g95GQu9uZpzJRUv/EzkBuKUH0deaH+elMRN+2WitOiiTECMFuz+jTSjdH8tOl0Xqqm+w4gYie8a2zCoUt6LrMxw9MPmnRkhhN4ijIByzZ8OSiGoLRJfx017pK0Apuf8YuyZopV2HIgL2cPMkNyUgjAgqKwj3m1JD6eNtNyFcCckAttUwF0RJBLXrhlKjkWKfx7piIB2TV21N7FFge365WQ54Rp2FJfQr54rwrdQ9MIE6RRiZ62W2Nd5zjwP+q76BbZUhMkz3wGBv23SE8z2QjE2TSPARtqGNK6Hx2/rBrhrRaq17KaB6iMnddYMTjPvbtQ3r2ORuukqRMcMUVactyzUJ8zj5IPUG8aCaveK69KUFTpB6fpaJ8k/gESOfgZ9cMET263EiD7Ma1qIh+UA0URlKOYTES8+xj+XF6tgS",
#     "encSecKey": "727ff2b299277b0f5e66b9c44c38938c765a340fe4df871dbb24591a5831e537807ef6ffc88d5463a2c807044cc85b4151c7d5023cfd682c0ce98e07ac05920497aa699c93654ce34acdb35982bdb4e04dcb92f3b6a616947d5f19a9d9395b5cd18482ef8f25d1bba9a328fb076d21a65c28b82b960b39a2a7b1ccfa70ca4e2e"
# }
    secKey = "SFWg3Dmd3YKpUfTJ"
    data = {
    "encText": "25nbNBdx3zCW4GWzp406TIy9wucJLxBAnpeNztKmYGU0mQ0s6qnay1yByT9qvoAva3yBEHg+B23mGQd3f86Sr4NgNwHi+HXJrPQk+9fnk5VCz10FFi09ReHdvh08PBv3X0p69dyDGRhAY9MASFNpKIjwnCCKrSQUcUMtonqwFuP/2ewBmLXkb0sdmtnpPFH9HIxsMZ71vF7lR0Pu1qiRdw==",
    "encSecKey": "7ee143b035bb7955f27b82400b7d366a32b49e4527ea531b66cc75173fffa947f0e7fc68058afbd8fdf92d403c0565231ff6bace28f04c9227c4a0d334ab8f78fee2d237da7ee6bd37823d0831a4aa725ef48ea5c897f1a3a03dcc37217ea2bdc6fa59d9b30247287438a280fad0710e218be1a1a97392ddbf1bc0ead6982d0e"
}
    encrypted_params = data["encText"]
    encSecKey = data["encSecKey"]
    
    print("=== 使用decrypt_params函数解密 ===")
    # 使用decrypt_params函数解密
    decrypted_text = decrypt_params(encrypted_params, secKey)
    print("解密后的文本:", decrypted_text)
    print("解密后的JSON对象:", json.loads(decrypted_text))
    
    print("\n=== 使用decrypt_with_known_seckey函数解密 ===")
    # 使用decrypt_with_known_seckey函数解密
    decrypted_obj = decrypt_with_known_seckey(encrypted_params, secKey)
    print("解密后的JSON对象:", decrypted_obj)