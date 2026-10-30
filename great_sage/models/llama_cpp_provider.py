"""llama.cpp behind the same ModelProvider seam.

llama.cpp's `llama-server` exposes an OpenAI-compatible API, and so do
the routers that sit in front of it (llama-swap), so this provider is a
thin specialization of OpenAIProvider rather than a reimplementation:

  - base_url points at the local server. The default is llama-server's
    OWN default port (8080), which is what a fresh GitHub checkout runs
    against.
  - the API key is optional - a bare llama-server needs none, a router
    such as llama-swap may require one (the apiKeys in its config).
  - an empty model name resolves to the first model the server reports,
    so a fresh checkout works against whatever GGUF is loaded without
    any editing.
  - think is sent as chat_template_kwargs={"enable_thinking": ...},
    which llama.cpp passes to the jinja chat template. Qwen3-family
    models honour it; other templates ignore the variable.

What llama.cpp does not have that Ollama did, and what this provider
therefore does without:

  - no per-request context sizing (the context is fixed at server start
    with -c), so the old num_ctx juggling is gone with it
  - no keep_alive, and no unload endpoint. An idle llama-swap instance
    is evicted by the router's own TTL; a bare llama-server IS the model
    and only a restart frees it. So unload() is a documented no-op.
"""

from great_sage.models.base import ModelProviderError
from great_sage.models.openai_provider import OpenAIProvider

DEFAULT_BASE_URL = "http://localhost:8080"  # llama-server's own default


class LlamaCppProvider(OpenAIProvider):
    def __init__(self, base_url: str = DEFAULT_BASE_URL, api_key: str = "",
                 model: str = "", timeout: int = 60, think: bool = False):
        super().__init__(api_key=api_key, model=model, timeout=timeout,
                         base_url=base_url, think=think,
                         name="the llama.cpp server")
        self._auto_model = None

    def _model(self) -> str:
        if self.model:
            return self.model
        if self._auto_model:
            return self._auto_model
        models = self.get_available_models()
        if not models:
            raise ModelProviderError(
                "No model name is set and the server reports none. "
                "Set LLM_DEFAULT_MODEL in settings.local.py.")
        self._auto_model = models[0]
        return self._auto_model

    def unload(self) -> bool:
        """llama.cpp has no unload endpoint, so there is nothing to call:
        llama-swap evicts an idle instance after its own TTL, and a bare
        llama-server is the model itself. The GAMING/SLEEP modes rely on
        that and on the frame-rate/listening limits doing the rest."""
        return True
