r"""An OpenAI-compatible chat API behind the same ModelProvider seam.

Two things sit behind this class:

  - api.openai.com itself (the "openai" entry in the HUD's AI settings)
  - any local OpenAI-compatible server: llama.cpp's `llama-server`, or a
    router in front of it such as llama-swap (the "llama_cpp" local
    provider - see llama_cpp_provider.py)

Shape differences from Ollama, absorbed here:

  - the system prompt is an ordinary message with role "system", which is
    the same as Ollama, so no splitting is needed (unlike Anthropic)
  - images travel as content PARTS with a data: URL, not as a top-level
    "images" list
  - tool results use role "tool" with a tool_call_id that must match the
    call, so the id is carried through rather than invented
"""

import json
import logging
from typing import Iterator, List

import requests

from great_sage.models.base import Message, ModelProvider, ModelProviderError

log = logging.getLogger(__name__)

# The SERVER ROOT, not the API root: both api.openai.com and a local
# llama.cpp server expose their OpenAI-compatible API under /v1.
DEFAULT_BASE_URL = "https://api.openai.com"
DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIProvider(ModelProvider):
    def __init__(self, api_key: str = "", model: str = "", timeout: int = 90,
                 base_url: str = DEFAULT_BASE_URL, think: bool = None,
                 name: str = "OpenAI"):
        """think is sent as chat_template_kwargs={"enable_thinking": ...}
        when it is not None - llama.cpp passes it into the model's jinja
        chat template, where Qwen3-family models honour it and templates
        that never reference the variable ignore it. None (the default)
        sends nothing, which is what api.openai.com wants.

        name is how the error messages refer to the server, because the
        same failure text serves both "OpenAI" and "the llama.cpp
        server".
        """
        if not api_key and base_url == DEFAULT_BASE_URL:
            raise ModelProviderError("No OpenAI API key is set.")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.think = think
        self.name = name

    def _model(self) -> str:
        return self.model or DEFAULT_MODEL

    @staticmethod
    def supports_tools() -> bool:
        return True

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def _convert(self, messages: List[Message]):
        out = []
        for m in messages:
            role = m.get("role")
            content = m.get("content", "")
            images = m.get("images")
            if role == "tool":
                out.append({"role": "tool",
                            "tool_call_id": m.get("tool_call_id") or "call_1",
                            "content": str(content)})
                continue
            if images:
                parts = [{"type": "text", "text": content or ""}]
                for img in images:
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + img}})
                out.append({"role": role or "user", "content": parts})
                continue
            entry = {"role": role or "user", "content": content}
            if m.get("tool_calls"):
                entry["tool_calls"] = m["tool_calls"]
            out.append(entry)
        return out

    def _body(self, messages: List[Message], **extra) -> dict:
        body = {"model": self._model(),
                "messages": self._convert(messages)}
        if self.think is not None:
            body["chat_template_kwargs"] = {"enable_thinking": bool(self.think)}
        body.update(extra)
        return body

    def _fail(self, exc, response=None):
        """Never let the key reach a log line or an error message."""
        if response is not None:
            if response.status_code == 401:
                return ModelProviderError(
                    "%s rejected the API key. Check it in the settings."
                    % self.name)
            if response.status_code == 429:
                return ModelProviderError(
                    "%s rate limit reached. Try again shortly." % self.name)
            return ModelProviderError(
                "%s returned HTTP %s." % (self.name, response.status_code))
        return ModelProviderError("Could not reach %s: %s"
                                  % (self.name, type(exc).__name__))

    def _post(self, body, stream=False):
        try:
            r = requests.post(self.base_url + "/v1/chat/completions",
                              headers=self._headers(), json=body,
                              timeout=self.timeout, stream=stream)
        except Exception as exc:
            raise self._fail(exc) from exc
        if r.status_code != 200:
            raise self._fail(None, r)
        return r

    def send_message(self, messages: List[Message]) -> str:
        r = self._post(self._body(messages))
        try:
            return r.json()["choices"][0]["message"].get("content") or ""
        except Exception as exc:
            raise ModelProviderError(
                "%s returned an unexpected response format." % self.name
            ) from exc

    def chat_raw(self, messages, tools=None):
        """Ollama-shaped message dict, so the tool loop needs no changes."""
        body = self._body(messages)
        if tools:
            body["tools"] = tools
        r = self._post(body)
        try:
            msg = r.json()["choices"][0]["message"]
        except Exception as exc:
            raise ModelProviderError(
                "%s returned an unexpected response format." % self.name
            ) from exc
        out = {"role": "assistant", "content": msg.get("content") or ""}
        calls = msg.get("tool_calls") or []
        if calls:
            # Translated into the shape ChatEngine already reads, so one
            # tool loop serves every provider.
            out["tool_calls"] = [{
                "id": c.get("id"),
                "function": {
                    "name": (c.get("function") or {}).get("name"),
                    "arguments": _parse_args(
                        (c.get("function") or {}).get("arguments")),
                }} for c in calls]
        return out

    def stream_response(self, messages: List[Message]) -> Iterator[str]:
        r = self._post(self._body(messages, stream=True), stream=True)
        for line in r.iter_lines():
            if not line or not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload in (b"", b"[DONE]"):
                continue
            try:
                delta = json.loads(payload)["choices"][0].get("delta") or {}
            except Exception:
                continue
            text = delta.get("content")
            if text:
                yield text

    def get_available_models(self) -> List[str]:
        """What the server reports. The /v1/models endpoint is the same on
        api.openai.com, a bare llama-server and a router such as
        llama-swap, so one fetch serves all three."""
        try:
            r = requests.get(self.base_url + "/v1/models",
                             headers=self._headers(), timeout=self.timeout)
        except Exception as exc:
            raise self._fail(exc) from exc
        if r.status_code != 200:
            raise self._fail(None, r)
        try:
            return sorted(m["id"] for m in r.json()["data"])
        except Exception as exc:
            raise ModelProviderError(
                "%s returned an unexpected response format." % self.name
            ) from exc

    def unload(self) -> bool:
        """Free the model from GPU memory, if the backend can.

        The OpenAI API has no unload, and for api.openai.com there is
        nothing local to free - so a no-op is correct here. The local
        provider documents its own reality in its override.
        """
        return True


def _parse_args(raw):
    """OpenAI sends arguments as a JSON STRING; Ollama sends a dict."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}
