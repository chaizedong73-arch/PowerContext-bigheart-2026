"""MiniMax 开放平台客户端：真实调用大模型完成编码任务。

所有请求/响应写入开发记录 minimax_dev_log.jsonl。API Key 只从
MINIMAX_API_KEY 环境变量读取，绝不写入仓库文件。
"""

import json
import os
import time
from pathlib import Path

import requests

from . import CtxsddError

API_URL = "https://api.minimax.io/v1/text/chatcompletion_v2"
DEFAULT_MODEL = "MiniMax-M3"


class MiniMaxError(CtxsddError):
    pass


class MiniMaxClient:
    def __init__(self, log_path: Path, model: str = DEFAULT_MODEL, api_key: str | None = None):
        self.model = model
        self.api_key = (api_key or os.environ.get("MINIMAX_API_KEY") or "").strip()
        if not self.api_key:
            raise MiniMaxError(
                "未设置 MINIMAX_API_KEY 环境变量。"
                "请先导出 MiniMax 开放平台 API Key：export MINIMAX_API_KEY=..."
            )
        self.log_path = Path(log_path)

    def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        body = {"model": self.model, "messages": messages, "temperature": temperature}
        start = time.time()
        try:
            resp = requests.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=300,
            )
        except requests.RequestException as e:
            self._log(body, None, None, int((time.time() - start) * 1000), {"exception": str(e)})
            raise MiniMaxError(f"MiniMax API 请求失败: {e}") from e

        latency = int((time.time() - start) * 1000)
        try:
            data = resp.json()
        except ValueError:
            self._log(body, None, None, latency, {"http_status": resp.status_code})
            raise MiniMaxError(f"MiniMax API 返回非 JSON（HTTP {resp.status_code}）")

        base = data.get("base_resp") or {}
        if base.get("status_code", 0) != 0:
            hint = ""
            if base.get("status_code") == 1008:
                hint = "（余额不足：请到 MiniMax 开放平台激活/充值 Token 包）"
            self._log(body, None, None, latency, base)
            raise MiniMaxError(
                f"MiniMax API 错误 status_code={base.get('status_code')} "
                f"status_msg={base.get('status_msg')}{hint}"
            )
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError):
            self._log(body, None, None, latency, data)
            raise MiniMaxError(f"MiniMax API 响应缺少 choices: {data}")
        content = message.get("content") or ""
        self._log(
            body,
            content,
            message.get("reasoning_content"),
            latency,
            base,
            usage=data.get("usage"),
        )
        if not content.strip():
            raise MiniMaxError("MiniMax API 返回空 content")
        return content

    def _log(self, request_body, content, reasoning, latency_ms, base_resp, usage=None):
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "endpoint": API_URL,
            "model": request_body.get("model"),
            "messages": request_body.get("messages"),
            "response_content": content,
            "reasoning_truncated": (reasoning or "")[:2000] or None,
            "usage": usage,
            "base_resp": base_resp,
            "latency_ms": latency_ms,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
