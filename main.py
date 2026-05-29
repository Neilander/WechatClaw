import json
import logging
import os
import traceback
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from openai import OpenAI
from pydantic import BaseModel, Field

from wecom_client import WeComClient
from wecom_crypto import WeComCrypto, WeComCryptoError, extract_encrypt, parse_message


load_dotenv()

DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL_NAME = "deepseek-chat"
DEFAULT_MAX_HISTORY_MESSAGES = 20
DEFAULT_SYSTEM_PROMPT = "你是一个运行在微信里的 AI 助手，回答要简洁、有帮助。"


def get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        parsed = int(value)
    except ValueError:
        logging.warning("Invalid %s=%s, using default=%s", name, value, default)
        return default

    if parsed < 1:
        logging.warning("Invalid %s=%s, using default=%s", name, value, default)
        return default

    return parsed


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("wechatclaw")

app = FastAPI(title="WechatClaw Phase 0.5 AI Backend MVP")

LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
MODEL_NAME = os.getenv("MODEL_NAME") or DEFAULT_MODEL_NAME
MAX_HISTORY_MESSAGES = get_int_env("MAX_HISTORY_MESSAGES", DEFAULT_MAX_HISTORY_MESSAGES)
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT
MEMORY_FILE = Path(os.getenv("MEMORY_FILE", "memory.json"))
MEMORY_LOCK = Lock()

if not LLM_API_KEY:
    raise RuntimeError("LLM_API_KEY is missing. Check your .env file.")

client = OpenAI(
    api_key=LLM_API_KEY,
    base_url=LLM_BASE_URL,
)

# ----- 企业微信客服配置 -----
WECOM_CORP_ID = os.getenv("WECOM_CORP_ID")
WECOM_KF_SECRET = os.getenv("WECOM_KF_SECRET")
WECOM_TOKEN = os.getenv("WECOM_TOKEN")
WECOM_ENCODING_AES_KEY = os.getenv("WECOM_ENCODING_AES_KEY")

CURSOR_FILE = Path(os.getenv("WECOM_CURSOR_FILE", "wecom_cursor.json"))
CURSOR_LOCK = Lock()
KF_SYNC_LOCK = Lock()

# 只有四个配置都齐全时才启用微信客服。缺任何一个就跳过，本地开发不受影响。
WECOM_ENABLED = all([WECOM_CORP_ID, WECOM_KF_SECRET, WECOM_TOKEN, WECOM_ENCODING_AES_KEY])

if WECOM_ENABLED:
    wecom_crypto = WeComCrypto(WECOM_TOKEN, WECOM_ENCODING_AES_KEY, WECOM_CORP_ID)
    wecom_client = WeComClient(WECOM_CORP_ID, WECOM_KF_SECRET)
    logger.info("WeCom 客服已启用")
else:
    wecom_crypto = None
    wecom_client = None
    logger.warning("WeCom 客服未启用：缺少 WECOM_* 环境变量，仅 /chat 可用")


class ChatRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    user_id: str
    answer: str
    history_length: int


class MemoryLengthResponse(BaseModel):
    user_id: str
    history_length: int


class DeleteMemoryResponse(BaseModel):
    deleted: bool
    user_id: str


def load_memory() -> dict:
    if not MEMORY_FILE.exists():
        return {}

    raw_memory = MEMORY_FILE.read_text(encoding="utf-8").strip()
    if not raw_memory:
        return {}

    try:
        data = json.loads(raw_memory)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="memory.json is not valid JSON") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="memory.json must contain a JSON object")

    return data


def save_memory(memory: dict) -> None:
    MEMORY_FILE.write_text(
        json.dumps(memory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def trim_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    return history[-MAX_HISTORY_MESSAGES:]


def call_llm(user_id: str, history: list[dict[str, str]]) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
        )
        answer = response.choices[0].message.content
    except Exception as exc:
        print("========== LLM ERROR ==========")
        print(str(exc))
        traceback.print_exc()
        print("================================")
        logger.warning(
            "llm_failed user_id=%s history_length=%s model=%s error_type=%s",
            user_id,
            len(history),
            MODEL_NAME,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "LLM request failed",
                "real_error": str(exc),
            },
        ) from exc

    logger.info(
        "llm_success user_id=%s history_length=%s model=%s",
        user_id,
        len(history),
        MODEL_NAME,
    )
    return answer or ""


def generate_reply(user_id: str, message: str) -> tuple[str, int]:
    """核心对话逻辑：读记忆 -> 调 LLM -> 写记忆。返回 (回复, 历史长度)。

    /chat 和微信客服回调共用这一段逻辑。
    """
    with MEMORY_LOCK:
        memory = load_memory()
        history = memory.setdefault(user_id, [])

        if not isinstance(history, list):
            raise HTTPException(status_code=500, detail=f"Invalid history for user_id: {user_id}")

        history.append({"role": "user", "content": message})
        history = trim_history(history)
        memory[user_id] = history

        logger.info("chat_request user_id=%s history_length=%s", user_id, len(history))

        answer = call_llm(user_id, history)
        history.append({"role": "assistant", "content": answer})
        history = trim_history(history)
        memory[user_id] = history
        save_memory(memory)

    return answer, len(history)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def load_cursor(open_kfid: str) -> str:
    """读取某个客服账号上次同步到的位置。"""
    with CURSOR_LOCK:
        if not CURSOR_FILE.exists():
            return ""
        raw = CURSOR_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        return data.get(open_kfid, "")


def save_cursor(open_kfid: str, cursor: str) -> None:
    with CURSOR_LOCK:
        data = {}
        if CURSOR_FILE.exists():
            raw = CURSOR_FILE.read_text(encoding="utf-8").strip()
            if raw:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = {}
        data[open_kfid] = cursor
        CURSOR_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def process_kf_message(msg: dict) -> None:
    """处理一条客服消息：只回复客户发来的文字消息。"""
    # origin: 3=客户发的, 4=系统推送, 5=接待人员发的。只处理 3，避免回复自己。
    if msg.get("origin") != 3 or msg.get("msgtype") != "text":
        return

    external_userid = msg.get("external_userid")
    open_kfid = msg.get("open_kfid")
    content = (msg.get("text") or {}).get("content", "").strip()
    if not external_userid or not open_kfid or not content:
        return

    answer, _ = generate_reply(external_userid, content)
    resp = wecom_client.send_text(external_userid, open_kfid, answer)
    if resp.get("errcode"):
        logger.warning("send_msg 失败 external_userid=%s resp=%s", external_userid, resp)


def handle_kf_event(token: str, open_kfid: str) -> None:
    """收到'有新消息'事件后，拉取并处理消息。在后台任务中运行。"""
    with KF_SYNC_LOCK:
        cursor = load_cursor(open_kfid)
        has_more = 1
        while has_more:
            resp = wecom_client.sync_msg(token=token, cursor=cursor, open_kfid=open_kfid)
            if resp.get("errcode"):
                logger.warning("sync_msg 失败 open_kfid=%s resp=%s", open_kfid, resp)
                return

            for msg in resp.get("msg_list", []):
                try:
                    process_kf_message(msg)
                except Exception:
                    logger.exception("处理单条消息出错 msgid=%s", msg.get("msgid"))

            cursor = resp.get("next_cursor", cursor)
            save_cursor(open_kfid, cursor)
            has_more = resp.get("has_more", 0)


@app.get("/wecom/callback")
async def wecom_verify(
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
):
    """企业微信配置回调 URL 时的验证握手。"""
    if not WECOM_ENABLED:
        return PlainTextResponse("wechatclaw ok (wecom disabled)")
    if not echostr:
        return PlainTextResponse("wechatclaw ok")

    try:
        plain = wecom_crypto.decrypt_message(msg_signature, timestamp, nonce, echostr)
    except WeComCryptoError as exc:
        logger.warning("回调验证失败: %s", exc)
        raise HTTPException(status_code=403, detail="verification failed")

    return PlainTextResponse(plain)


@app.post("/wecom/callback")
async def wecom_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
):
    """接收企业微信推送的消息事件。"""
    if not WECOM_ENABLED:
        raise HTTPException(status_code=503, detail="wecom disabled")

    body = (await request.body()).decode("utf-8")

    try:
        encrypt = extract_encrypt(body)
        xml_text = wecom_crypto.decrypt_message(msg_signature, timestamp, nonce, encrypt)
    except WeComCryptoError as exc:
        logger.warning("回调消息解密失败: %s", exc)
        raise HTTPException(status_code=403, detail="decrypt failed")

    event = parse_message(xml_text)

    # 微信客服的新消息事件，Event=kf_msg_or_event，带 Token 和 OpenKfId。
    if event.get("Event") == "kf_msg_or_event":
        token = event.get("Token", "")
        open_kfid = event.get("OpenKfId", "")
        if token and open_kfid:
            # 后台拉取处理，先立刻返回，避免微信 5 秒超时重推。
            background_tasks.add_task(handle_kf_event, token, open_kfid)

    return PlainTextResponse("success")


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    user_id = payload.user_id.strip()
    message = payload.message.strip()

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id cannot be empty")
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    answer, history_length = generate_reply(user_id, message)

    return ChatResponse(
        user_id=user_id,
        answer=answer,
        history_length=history_length,
    )


@app.get("/memory/{user_id}", response_model=MemoryLengthResponse)
def get_memory_length(user_id: str) -> MemoryLengthResponse:
    user_id = user_id.strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id cannot be empty")

    with MEMORY_LOCK:
        memory = load_memory()
        history = memory.get(user_id, [])

        if not isinstance(history, list):
            raise HTTPException(status_code=500, detail=f"Invalid history for user_id: {user_id}")

    return MemoryLengthResponse(user_id=user_id, history_length=len(history))


@app.delete("/memory/{user_id}", response_model=DeleteMemoryResponse)
def delete_memory(user_id: str) -> DeleteMemoryResponse:
    user_id = user_id.strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id cannot be empty")

    with MEMORY_LOCK:
        memory = load_memory()
        memory.pop(user_id, None)
        save_memory(memory)

    logger.info("memory_deleted user_id=%s", user_id)
    return DeleteMemoryResponse(deleted=True, user_id=user_id)
