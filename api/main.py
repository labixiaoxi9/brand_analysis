import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Generator, Optional

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="Coze Agent Proxy")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

COZE_STREAM_URL = os.getenv("COZE_STREAM_RUN_URL", "https://kcbnb7vzxq.coze.site/stream_run")
COZE_SESSION_ID = os.getenv("COZE_SESSION_ID", "")
COZE_PROJECT_ID = os.getenv("COZE_PROJECT_ID", "")
COZE_API_TOKEN = os.getenv("COZE_API_TOKEN", "")

def _build_payload(user_text: str) -> Dict:
    return {
        "content": {
            "query": {
                "prompt": [
                    {
                        "type": "text",
                        "content": {"text": user_text.strip()},
                    }
                ]
            }
        },
        "type": "query",
        "session_id": COZE_SESSION_ID,
        "project_id": COZE_PROJECT_ID,
    }


def _iter_sse_data(response: requests.Response) -> Generator[Dict, None, None]:
    for line in response.iter_lines(decode_unicode=True):
        if not line:
            continue

        if line.startswith("event:"):
            # 当前逻辑主要依赖 data 行，event 行可忽略
            continue

        if not line.startswith("data:"):
            continue

        data_text = line[5:].strip()
        if not data_text:
            continue

        if data_text == "[DONE]":
            yield {"event": "done", "data": "[DONE]"}
            continue

        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError:
            yield {"event": "raw", "data": data_text}
            continue

        if isinstance(payload, dict):
            yield payload
        else:
            yield {"event": "raw", "data": payload}


def _extract_text_from_any(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text:
            return text

        content = value.get("content")
        if isinstance(content, dict):
            ctext = content.get("text")
            if isinstance(ctext, str) and ctext:
                return ctext

        for key in ("answer", "output", "message"):
            v = value.get(key)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, dict):
                vt = v.get("text")
                if isinstance(vt, str) and vt:
                    return vt

    if isinstance(value, list):
        return "".join(_extract_text_from_any(item) for item in value)

    return ""


def _extract_answer_from_sse_obj(obj: Dict[str, Any]) -> Optional[str]:
    if not isinstance(obj, dict):
        return None

    candidate = obj.get("data", obj)
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError:
            return None

    if not isinstance(candidate, dict):
        return None

    # 优先严格提取 answer，减少把中间检索信息当最终答案
    ctype = str(candidate.get("type", "")).lower()
    if ctype not in {"answer", "final_answer", "output_text"}:
        return None

    text = _extract_text_from_any(candidate.get("content", {}))
    if text:
        return text

    root_text = _extract_text_from_any(candidate)
    return root_text or None


def _extract_texts(obj) -> list[str]:
    texts = []
    if isinstance(obj, str):
        if obj.strip():
            texts.append(obj)
        return texts

    if isinstance(obj, list):
        for item in obj:
            texts.extend(_extract_texts(item))
        return texts

    if not isinstance(obj, dict):
        return texts

    for key in ("content", "text", "answer", "message", "output"):
        if key in obj:
            texts.extend(_extract_texts(obj[key]))

    for key in ("delta", "chunk", "payload", "data"):
        if key in obj:
            texts.extend(_extract_texts(obj[key]))

    if "type" in obj and str(obj.get("type", "")).lower() in {"text", "answer"}:
        if "content" in obj and isinstance(obj["content"], str):
            texts.append(obj["content"])
        if "text" in obj and isinstance(obj["text"], str):
            texts.append(obj["text"])

    return texts


def _normalize_answer_text(text: str) -> str:
    if not text:
        return ""

    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _build_headers() -> Dict[str, str]:
    token = COZE_API_TOKEN
    if not token:
        raise HTTPException(status_code=500, detail="缺少 COZE_API_TOKEN，请在 .env 中配置")
    if not COZE_SESSION_ID:
        raise HTTPException(status_code=500, detail="缺少 COZE_SESSION_ID，请在 .env 中配置")
    if not COZE_PROJECT_ID:
        raise HTTPException(status_code=500, detail="缺少 COZE_PROJECT_ID，请在 .env 中配置")

    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


def _extract_delta_text(event_item: Dict) -> str:
    if not isinstance(event_item, dict):
        return ""

    data = event_item.get("data")
    if isinstance(data, dict):
        # 常见流式字段优先
        for key in ("delta", "content", "text", "answer"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val

    texts = _extract_texts(event_item)
    if texts:
        return texts[0]
    return ""


def call_coze_agent(user_text: str) -> Dict:
    headers = _build_headers()
    payload = _build_payload(user_text)

    try:
        response = requests.post(COZE_STREAM_URL, headers=headers, json=payload, stream=True, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"调用 Coze 接口失败: {exc}") from exc

    answer_chunks: list[str] = []
    events = []
    last_piece: Optional[str] = None

    for event_item in _iter_sse_data(response):
        events.append(event_item)

        piece = _extract_answer_from_sse_obj(event_item)
        if piece and piece != last_piece:
            answer_chunks.append(piece)
            last_piece = piece

    final_answer_raw = "".join(answer_chunks).strip()
    final_answer = _normalize_answer_text(final_answer_raw)

    if not final_answer:
        raise HTTPException(status_code=502, detail="未解析到 agent 最终 answer，请检查 COZE_STREAM_RUN_URL/SESSION/PROJECT 配置")

    return {
        "ok": True,
        "answer": final_answer,
        "events": events,
    }


def stream_coze_agent(user_text: str) -> Generator[str, None, None]:
    headers = _build_headers()
    payload = _build_payload(user_text)

    try:
        response = requests.post(
            COZE_STREAM_URL,
            headers=headers,
            json=payload,
            stream=True,
            timeout=(10, 300),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        err = f"调用 Coze 接口失败: {exc}"
        yield f"data: {json.dumps({'type': 'error', 'message': err}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        return

    seen = set()
    try:
        for event_item in _iter_sse_data(response):
            if event_item.get("event") == "done":
                break

            text = _extract_delta_text(event_item)
            if not text:
                continue

            # 简单去重，避免部分接口重复推送整段
            key = (text[:80], len(text))
            if key in seen:
                continue
            seen.add(key)

            yield f"data: {json.dumps({'type': 'delta', 'text': text}, ensure_ascii=False)}\n\n"
    except requests.RequestException as exc:
        err = f"流式读取中断: {exc}"
        yield f"data: {json.dumps({'type': 'error', 'message': err}, ensure_ascii=False)}\n\n"

    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/chat")
def chat(text: str = Form(...)):
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    result = call_coze_agent(text)
    return JSONResponse(content=result)


@app.get("/api/chat/stream")
def chat_stream(text: str):
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    return StreamingResponse(
        stream_coze_agent(text),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}
