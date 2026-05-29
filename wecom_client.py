"""企业微信客服 API 客户端：管理 access_token、拉取消息、发送回复。

微信客服的消息流转：
  1. 用户发消息 -> 微信只推一个"有新消息"事件(带 token)到我们的回调
  2. 我们拿 token 调 sync_msg 把真实消息拉回来
  3. 处理后调 send_msg 把回复发出去
"""

import logging
import time
from threading import Lock

import requests

logger = logging.getLogger("wechatclaw.wecom")

BASE_URL = "https://qyapi.weixin.qq.com/cgi-bin"

# access_token 失效相关的错误码，命中后强制刷新并重试一次。
TOKEN_ERROR_CODES = {40014, 42001}


class WeComClient:
    def __init__(self, corp_id: str, secret: str):
        self.corp_id = corp_id
        self.secret = secret
        self._token = None
        self._expire_at = 0.0
        self._lock = Lock()

    def _fetch_token(self) -> str:
        resp = requests.get(
            f"{BASE_URL}/gettoken",
            params={"corpid": self.corp_id, "corpsecret": self.secret},
            timeout=10,
        ).json()
        if resp.get("errcode"):
            raise RuntimeError(f"gettoken 失败: {resp}")
        with self._lock:
            self._token = resp["access_token"]
            # 提前 5 分钟过期，留出余量。
            self._expire_at = time.time() + resp.get("expires_in", 7200) - 300
        return self._token

    def get_token(self, force_refresh: bool = False) -> str:
        if not force_refresh and self._token and time.time() < self._expire_at:
            return self._token
        return self._fetch_token()

    def _post(self, path: str, body: dict) -> dict:
        """带 access_token 的 POST，token 失效时自动刷新重试一次。"""
        for attempt in range(2):
            token = self.get_token(force_refresh=(attempt == 1))
            resp = requests.post(
                f"{BASE_URL}/{path}",
                params={"access_token": token},
                json=body,
                timeout=15,
            ).json()
            if resp.get("errcode") in TOKEN_ERROR_CODES and attempt == 0:
                logger.warning("access_token 失效，刷新后重试: %s", resp)
                continue
            return resp
        return resp

    def sync_msg(self, token: str, cursor: str = "", open_kfid: str = "") -> dict:
        body = {"token": token, "limit": 1000, "cursor": cursor}
        if open_kfid:
            body["open_kfid"] = open_kfid
        return self._post("kf/sync_msg", body)

    def send_text(self, touser: str, open_kfid: str, content: str) -> dict:
        body = {
            "touser": touser,
            "open_kfid": open_kfid,
            "msgtype": "text",
            "text": {"content": content},
        }
        return self._post("kf/send_msg", body)

    def get_service_state(self, open_kfid: str, external_userid: str) -> dict:
        return self._post(
            "kf/service_state/get",
            {"open_kfid": open_kfid, "external_userid": external_userid},
        )

    def trans_service_state(
        self,
        open_kfid: str,
        external_userid: str,
        service_state: int,
        servicer_userid: str = "",
    ) -> dict:
        body = {
            "open_kfid": open_kfid,
            "external_userid": external_userid,
            "service_state": service_state,
        }
        if servicer_userid:
            body["servicer_userid"] = servicer_userid
        return self._post("kf/service_state/trans", body)
