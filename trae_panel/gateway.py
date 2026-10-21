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
            models.append({"name": c["name"],
                           "display_name": c.get("display_name") or c["name"],
                           "preset": c.get("is_preset")})   # True=内置预设, False=自定义, None=未知
    _model_cache[key] = {"at": time.time(), "models": models}
    return models


def _fallback_model(gw: dict, models: list, requested: str = "") -> str:
    """兜底模型：配置的 default_model > 与请求名同系列的内置预设 > 首个内置预设 > 清单首个。

    「同系列」= 模型名里有共同词（如 deepseek-v4-flash 与 deepseek-V3 都含 deepseek），
    这样用户填了一个本账号没有的名字时，会尽量回到同厂商的模型上。
    """
    fb = (gw.get("default_model") or "").strip()
    if fb:
        return fb
    cand = [m for m in models if m.get("name")]
    want = _name_tokens(requested)
    cand.sort(key=lambda m: (0 if (want & _name_tokens(m["name"])) else 1,
                             not m.get("preset"), m["name"]))
    return cand[0]["name"] if cand else ""


def resolve_model(requested: str, models: list, cfg: dict) -> str:
    """把客户端请求的模型名解析成上游 model_name。

    优先级：别名 > 精确命中 > 忽略大小写命中 > 模糊命中（优先内置预设）> 兜底模型。

    注意：**绝不**在「清单里没有这个名字」时把原始字符串直接透传给上游——Trae 上游
    只认内置预设模型的 model_name，透传自定义名（如 `cn:xxx`）会得到
    "the model is unknown"。清单为空/取不到时返回空，由调用方决定怎么报错。
    """
    gw = cfg.get("gateway") or {}
    aliases = gw.get("model_aliases") or {}
    names = [m["name"] for m in models]
    if not requested:
        return _fallback_model(gw, models)
    if requested in aliases:
        return aliases[requested]
    if requested in names:
        return requested
    low = requested.lower()
    # 精确忽略大小写
    for m in models:
        if m["display_name"].lower() == low or m["name"].lower() == low:
            return m["name"]
    # 模糊命中：内置预设优先，避免把自定义名解析到另一个自定义名
    for m in sorted(models, key=lambda x: not x.get("preset")):
        nm, dn = m["name"].lower(), m["display_name"].lower()
        if low in nm or low in dn or nm in low or dn in low:
            return m["name"]
    return _fallback_model(gw, models, requested)


def is_unknown_model(err) -> bool:
    """上游是否因「模型名不认识」而拒绝（Trae 对自定义/第三方模型名会这么回）。"""
    s = str(err).lower()
    return "unknown" in s or "不支持的模型" in s


def fallback_model(cfg: dict, models: list) -> str:
    """给 web 层用的兜底模型（与 resolve_model 末段同一口径）。"""
    return _fallback_model(cfg.get("gateway") or {}, models)


def _name_tokens(name: str) -> set:
    """把模型名切成 >=3 字符的字母数字词（deepseek-v4-flash → {deepseek, flash}）。"""
    out, buf = set(), ""
    for ch in (name or "").lower():
        if ch.isalnum():
            buf += ch
        else:
            if len(buf) >= 3:
                out.add(buf)
            buf = ""
    if len(buf) >= 3:
        out.add(buf)
    return out


def fallback_candidates(models: list, exclude: str, cfg: dict, limit: int = 3) -> list:
    """上游拒绝了 `exclude` 时，按优先级给出可回退的模型名（最多 limit 个）。

    Trae 只认账号清单里的 model_name。用户填的名字可能不在清单里（如
    `deepseek-v4-flash` 在部分账号上根本不存在），这时依次试清单里的模型，
    比直接报「model is unknown」有用得多。排序：配置的兜底模型 > 同系列
    （模型名有共同词，如 deepseek）> 内置预设 > 其他。
    """
    gw = cfg.get("gateway") or {}
    out = []
    fb = (gw.get("default_model") or "").strip()
    if fb and fb != exclude:
        out.append(fb)
    want = _name_tokens(exclude)
    cand = [m for m in models if m.get("name") and m["name"] != exclude]
    cand.sort(key=lambda m: (0 if (want & _name_tokens(m["name"])) else 1,
                             not m.get("preset")))
    for m in cand:
        n = m["name"]
        if n not in out:
            out.append(n)
        if len(out) >= limit:
            break
    return out


def available_presets(models: list, limit: int = 8) -> list:
    """当前账号可用的模型名（内置预设优先），用于报错时告诉用户真实可选项。"""
    presets = [m["name"] for m in models if m.get("preset")]
    rest = [m["name"] for m in models if not m.get("preset")]
    return (presets + rest)[:limit]


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
    # 按字节读、显式 UTF-8 解码：上游 SSE 常返回 `text/event-stream`（不带 charset），
    # requests 会默认按 ISO-8859-1 解，用 decode_unicode=True 会把中文解成乱码。
    for raw_line in r.iter_lines(decode_unicode=False):
        if raw_line is None:
            continue
        line = raw_line.decode("utf-8", "replace").strip()
        if not line:
            continue
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


# ---- API 型平台（OpenAI 兼容上游直通）----
def api_upstreams() -> list:
    """返回所有 API 型平台（kind=api）的账号：每个账号即一个上游（base_url + API Key）。"""
    from . import platforms
    from .store import load_accounts
    out = []
    for a in load_accounts():
        pid = a.get("platform") or "trae"
        if platforms.exists(pid) and platforms.get(pid).get("kind") == "api":
            out.append(a)
    return out


def api_models() -> list:
    """API 型上游可用的模型（对外 id 形如 `deepseek/deepseek-chat`）。"""
    out = []
    for a in api_upstreams():
        pid = a.get("platform")
        for m in (a.get("models") or []):
            out.append({"id": "{}/{}".format(pid, m), "platform": pid, "model": m,
                        "label": a.get("label") or pid})
    return out


def resolve_api_upstream(requested: str):
    """把请求的模型名解析到某个 API 型上游；返回 (account, upstream_model) 或 (None, None)。

    接受 `平台id/模型名` 与裸模型名两种写法。
    """
    req = (requested or "").strip()
    if not req:
        return None, None
    for a in api_upstreams():
        pid = a.get("platform")
        for m in (a.get("models") or []):
            if req == m or req == "{}/{}".format(pid, m):
                return a, m
    return None, None


def openai_upstream_chat(account: dict, model: str, messages: list, stream: bool, cfg: dict):
    """直通调用 OpenAI 兼容上游（上游本身就是 OpenAI 协议，无需转换）。"""
    base = (account.get("host") or "").rstrip("/")
    if not base:
        raise GatewayError("该上游未配置 base_url")
    url = base + "/chat/completions"
    headers = {
        "Authorization": "Bearer " + str(account.get("token") or ""),
        "Content-Type": "application/json",
    }
    timeout = int((cfg.get("gateway") or {}).get("timeout") or 300)
    body = {"model": model, "messages": messages, "stream": bool(stream)}
    try:
        return requests.post(url, headers=headers, json=body, stream=bool(stream), timeout=timeout)
    except requests.RequestException as e:
        raise GatewayError("连接上游失败：{}".format(e))


def relay_stream(resp):
    """把上游 SSE 原样转出（按 UTF-8 显式解码，避免 requests 用 latin-1 解出乱码）。"""
    for raw in resp.iter_lines(decode_unicode=False, delimiter=b"\n"):
        if raw is None:
            continue
        yield raw.decode("utf-8", "replace") + "\n"
