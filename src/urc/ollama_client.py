from __future__ import annotations

import asyncio
import json
import os
from itertools import cycle
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaRouter:
    def __init__(self, endpoints_cfg: dict) -> None:
        eps = endpoints_cfg.get("endpoints", [])
        self._endpoints: list[dict[str, Any]] = [e for e in eps if e.get("enabled", False)]
        if not self._endpoints:
            raise ValueError("No enabled Ollama endpoints configured")
        self._cycler = cycle(self._endpoints)

    async def chat_one_model(self, model: str, prompt: str, temperature: float, max_tokens: int) -> tuple[str, str]:
        errors: list[str] = []
        for _ in range(len(self._endpoints)):
            ep = next(self._cycler)
            base_url = ep["base_url"].rstrip("/")
            timeout = float(ep.get("timeout_sec", 90))
            if base_url.endswith("/api"):
                candidate_urls = [f"{base_url}/chat", f"{base_url}/api/chat"]
            else:
                candidate_urls = [f"{base_url}/api/chat", f"{base_url}/chat"]
            model_name = model
            if base_url.endswith("/api") and model_name.endswith(":cloud"):
                model_name = model_name[: -len(":cloud")]
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            endpoint_error: str | None = None
            for url in candidate_urls:
                for retry in range(3):
                    try:
                        headers = {"Content-Type": "application/json"}
                        api_key_env = ep.get("api_key_env")
                        if isinstance(api_key_env, str) and api_key_env.strip():
                            api_key = os.getenv(api_key_env.strip(), "").strip()
                            if api_key:
                                headers["Authorization"] = f"Bearer {api_key}"
                        req = Request(url=url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
                        data = await asyncio.to_thread(self._read_json, req, timeout)
                        return data.get("message", {}).get("content", ""), model
                    except Exception as exc:  # noqa: BLE001
                        msg = str(exc).lower()
                        if any(x in msg for x in ("429", "timed out", "timeout", "http 500")) and retry < 2:
                            await asyncio.sleep(0.5 * (2 ** retry))
                            continue
                        endpoint_error = f"model={model} endpoint={base_url} url={url}: {exc}"
                        if "http 404" in msg and ("/api/chat" in url or "/chat" in url):
                            break
                        retry = 3
                        break
                if endpoint_error and "http 404" in endpoint_error.lower():
                    continue
                if endpoint_error:
                    break
            if endpoint_error:
                errors.append(endpoint_error)
            await asyncio.sleep(0.2)
        raise RuntimeError("All endpoints failed for model " + model + ": " + " | ".join(errors))

    async def chat_with_fallback(self, models: list[str], prompt: str, temperature: float, max_tokens: int) -> tuple[str, str]:
        errors: list[str] = []
        for model in models:
            try:
                return await self.chat_one_model(model=model, prompt=prompt, temperature=temperature, max_tokens=max_tokens)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        raise RuntimeError("All models failed: " + " | ".join(errors))

    @staticmethod
    def _read_json(req: Request, timeout: float) -> dict[str, Any]:
        try:
            with urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"http {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"url error: {exc.reason}") from exc
