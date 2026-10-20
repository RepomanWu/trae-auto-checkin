# -*- coding: utf-8 -*-
"""OpenAI 兼容网关：把 Trae 账号的对话能力暴露为 /v1/chat/completions。

上游协议（逆向自 Trae CN 客户端，2026-10 在本机实测可用）：
    推理 host  : https://trae-api-cn.mchost.guru   （另有 /api/ide/v1/llm_raw_chat 备选）
    对话端点   : POST /api/ide/v1/chat
    请求体     : {user_input, intent_name, chat_history, context_resolvers,
                  session_id, conversation_id, model_name, multi_media}
    返回       : SSE，事件 progress_notice / prompt / metadata / output / error / done
                 output.data = {response, reasoning_content, tool_calls, multimodal_contents, phase}
    模型清单   : GET /api/ide/v1/model_list?type=chat -> {model_configs:[{name, display_name, ...}]}

注意：对话接口对**设备指纹**敏感——随机 device/machine id 会被上游按风控返回 4011 限流，
必须用客户端真实的 telemetry.machineId 与 iCubeAuthInfo://icube-dc:<n>（导入账号时一并保存）。
"""

from __future__ import annotations

import json
import time
import uuid

import requests

from .crypto import build_headers

INFER_HOST_DEFAULT = "https://trae-api-cn.mchost.guru"
CHAT_PATH = "/api/ide/v1/chat"
MODEL_LIST_PATH = "/api/ide/v1/model_list"


class GatewayError(Exception):
    pass


def infer_host(cfg: dict) -> str:
    return ((cfg.get("gateway") or {}).get("infer_host") or INFER_HOST_DEFAULT).rstrip("/")


def _headers(account: dict, device_cfg: dict, stream: bool) -> dict:
    h = build_headers(account, device_cfg)
    h["Accept"] = "text/event-stream" if stream else "application/json"
    return h


# ---- 模型清单 ----
_model_cache: dict = {}


def fetch_models(account: dict, device_cfg: dict, cfg: dict, use_cache: bool = True) -> list:
    """取某账号可用模型（name / display_name）。带 10 分钟缓存。"""
    key = account.get("id") or account.get("uid") or "?"
    cached = _model_cache.get(key)
    if use_cache and cached and time.time() - cached["at"] < 600:
        return cached["models"]
    r = requests.get(infer_host(cfg) + MODEL_LIST_PATH, params={"type": "chat"},
                     headers=_headers(account, device_cfg, stream=False), timeout=30)
    if r.status_code != 200:
        raise GatewayError("获取模型列表失败：HTTP {} {}".format(r.status_code, (r.text or "")[:150]))
    models = []
    for c in (r.json() or {}).get("model_configs") or []:
        if c.get("name"):
            models.append({"name": c["name"], "display_name": c.get("display_name") or c["name"]})
    _model_cache[key] = {"at": time.time(), "models": models}
    return models


def resolve_model(requested: str, models: list, cfg: dict) -> str:
    """把客户端请求的模型名解析成上游 model_name。"""
    gw = cfg.get("gateway") or {}
    aliases = gw.get("model_aliases") or {}
    names = [m["name"] for m in models]
    if not requested:
        return gw.get("default_model") or (names[0] if names else "")
    if requested in aliases:
        return aliases[requested]
    if requested in names:
        return requested
    low = requested.lower()
    for m in models:                       # display_name 模糊匹配
        if m["display_name"].lower() == low or m["name"].lower() == low:
            return m["name"]
    for m in models:
        if low in m["display_name"].lower() or low in m["name"].lower():
            return m["name"]
    return gw.get("default_model") or (names[0] if names else requested)


# ---- 上游对话 ----
def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                out.append(b.get("text") or b.get("content") or "")
            elif isinstance(b, str):
                out.append(b)
        return " ".join(x for x in out if x)
    return ""


def flatten_messages(messages: list) -> str:
    """把 OpenAI messages 折叠成上游的 user_input 文本（带轻量角色标注）。"""
    parts = []
    for m in messages or []:
        text = _content_text(m.get("content"))
        if not text:
            continue
        role = m.get("role")
        if role == "system":
            parts.append("[system] " + text)
        elif role == "assistant":
            parts.append("[assistant] " + text)
        else:
            parts.append(text)
    return "\n\n".join(parts)


def build_body(messages: list, model_name: str, intent: str) -> dict:
    sid = str(uuid.uuid4())
    return {
        "user_input": flatten_messages(messages),
        "intent_name": intent,
        "chat_history": [],
        "context_resolvers": [],
        "session_id": sid,
        "conversation_id": str(uuid.uuid4()),
        "model_name": model_name,
        "multi_media": [],
    }


def chat_events(account: dict, device_cfg: dict, model_name: str, messages: list,
                intent: str, cfg: dict, timeout: int = 300):
    """生成器：向上游发起对话，逐个 yield (event, data)。"""
    body = build_body(messages, model_name, intent)
    r = requests.post(infer_host(cfg) + CHAT_PATH, headers=_headers(account, device_cfg, True),
                      json=body, stream=True, timeout=timeout)
    if r.status_code != 200:
        raise GatewayError("上游 HTTP {} {}".format(r.status_code, (r.text or "")[:200]))
    event = None
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            continue
        line = line.strip()
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            raw = line[5:].strip()
            try:
                data = json.loads(raw)
            except Exception:
                data = {"raw": raw}
            yield event, data
            event = None


def open_stream(accounts: list, device_cfg: dict, model_name: str, messages: list,
                intent: str, cfg: dict):
    """按顺序尝试账号，返回 (account, first_events, rest_iter)。

    在返回 HTTP 响应前先把上游跑通（拿到首个内容事件），失败则换下一个账号，
    这样客户端能拿到正确的 200/错误码，而不是"200 但内容是错误信息"。
    """
    gw = cfg.get("gateway") or {}
    req_timeout = int(gw.get("timeout") or 300)
    last_err = None
    for acc in accounts:
        try:
            it = chat_events(acc, device_cfg, model_name, messages, intent, cfg, timeout=req_timeout)
            first = []
            for event, data in it:
                if event == "error":
                    raise GatewayError(data.get("message") or "上游返回错误")
                if event == "output" and (data.get("response") or data.get("reasoning_content")):
                    first.append((event, data))
                    break
                if event == "done":
                    break
            return acc, first, it
        except Exception as e:
            last_err = e
            continue
    raise GatewayError("所有账号均失败：{}".format(last_err))


# ---- OpenAI 格式转换 ----
def _chunk(model: str, delta: dict, finish=None) -> str:
    payload = {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return "data: {}\n\n".format(json.dumps(payload, ensure_ascii=False))


def openai_chunks(model: str, first_events, rest_iter):
    """把上游事件流翻译成 OpenAI SSE 文本（生成器）。"""
    yield _chunk(model, {"role": "assistant", "content": ""})
    reasoning_open = False

    def convert(event, data):
        nonlocal reasoning_open
        out = []
        if event == "output":
            rc = data.get("reasoning_content") or ""
            resp = data.get("response") or ""
            if rc:
                out.append(_chunk(model, {"content": ("<think>\n" if not reasoning_open else "") + rc}))
                reasoning_open = True
            if resp:
                prefix = "</think>\n\n" if reasoning_open else ""
                reasoning_open = False
                out.append(_chunk(model, {"content": prefix + resp}))
        return out

    for event, data in list(first_events):
        for c in convert(event, data):
            yield c
    for event, data in rest_iter:
        if event == "error":
            yield "data: {}\n\n".format(json.dumps(
                {"error": {"message": data.get("message") or "upstream error", "type": "upstream_error"}}))
            return
        for c in convert(event, data):
            yield c
        if event == "done":
            yield _chunk(model, {}, finish=data.get("finish_reason") or "stop")
            yield "data: [DONE]\n\n"
            return
    yield _chunk(model, {}, finish="stop")
    yield "data: [DONE]\n\n"


def collect_non_stream(model: str, first_events, rest_iter) -> dict:
    """非流式：聚合上游事件为单个 OpenAI 响应。"""
    content, reasoning = [], []

    def take(event, data):
        if event == "output":
            if data.get("reasoning_content"):
                reasoning.append(data["reasoning_content"])
            if data.get("response"):
                content.append(data["response"])

    for event, data in list(first_events):
        take(event, data)
    finish = "stop"
    for event, data in rest_iter:
        take(event, data)
        if event == "error":
            raise GatewayError(data.get("message") or "上游返回错误")
        if event == "done":
            finish = data.get("finish_reason") or "stop"
            break
    text = ""
    if reasoning:
        text += "<think>\n{}\n</think>\n\n".format("".join(reasoning))
    text += "".join(content)
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
