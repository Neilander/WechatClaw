import json
import logging
import os
import traceback
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from openai import OpenAI
from pydantic import BaseModel, Field


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    user_id = payload.user_id.strip()
    message = payload.message.strip()

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id cannot be empty")
    if not message:
        raise HTTPException(status_code=400, detail="message cannot be empty")

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

    return ChatResponse(
        user_id=user_id,
        answer=answer,
        history_length=len(history),
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
