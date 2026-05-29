"""企业微信回调消息的签名校验与解密。

微信客服的回调消息是加密的，需要用后台配置的 Token + EncodingAESKey
来校验签名并解密。算法参考企业微信官方文档的 WXBizMsgCrypt。
"""

import base64
import hashlib
import socket
import struct
import xml.etree.ElementTree as ET

from Crypto.Cipher import AES


class WeComCryptoError(Exception):
    """签名校验或解密失败时抛出。"""


class WeComCrypto:
    def __init__(self, token: str, encoding_aes_key: str, receive_id: str):
        self.token = token
        # EncodingAESKey 是 43 位，补一个 "=" 后做 base64 解码得到 32 字节的 AES key。
        self.key = base64.b64decode(encoding_aes_key + "=")
        if len(self.key) != 32:
            raise WeComCryptoError("EncodingAESKey 解码后不是 32 字节，请检查配置")
        self.receive_id = receive_id

    def _signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        items = sorted([self.token, timestamp, nonce, encrypt])
        raw = "".join(items).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    def verify_signature(self, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> bool:
        return self._signature(timestamp, nonce, encrypt) == msg_signature

    def decrypt(self, encrypt: str) -> str:
        # AES-256-CBC，IV 取 key 的前 16 字节。
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        plain = cipher.decrypt(base64.b64decode(encrypt))

        # 去掉 PKCS#7 填充。
        pad = plain[-1]
        plain = plain[:-pad]

        # 明文结构：16 字节随机数 + 4 字节消息长度(网络字节序) + 消息 + receive_id
        content = plain[16:]
        msg_len = socket.ntohl(struct.unpack("I", content[:4])[0])
        msg = content[4:4 + msg_len].decode("utf-8")
        receive_id = content[4 + msg_len:].decode("utf-8")

        if self.receive_id and receive_id != self.receive_id:
            raise WeComCryptoError("receive_id 不匹配，可能 CorpID 配置错误")

        return msg

    def decrypt_message(self, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> str:
        if not self.verify_signature(msg_signature, timestamp, nonce, encrypt):
            raise WeComCryptoError("签名校验失败")
        return self.decrypt(encrypt)


def extract_encrypt(xml_body: str) -> str:
    """从回调 POST 的 XML body 里取出 <Encrypt> 密文。"""
    root = ET.fromstring(xml_body)
    node = root.find("Encrypt")
    if node is None or not node.text:
        raise WeComCryptoError("回调 body 里没有 Encrypt 字段")
    return node.text


def parse_message(xml_text: str) -> dict:
    """把解密后的消息 XML 解析成 dict。"""
    root = ET.fromstring(xml_text)
    return {child.tag: (child.text or "") for child in root}
