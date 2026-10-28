"""
OpenRouter service — free tier, 25+ models, no credit card.

API: OpenAI-compatible at https://openrouter.ai/api/v1
Docs: https://openrouter.ai/docs
"""

from openai import OpenAI
from app.core.config import settings

_client = OpenAI(
    api_key=getattr(settings, "OPENROUTER_API_KEY", "") or "",
    base_url="https://openrouter.ai/api/v1",
    default_headers={
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "Talk to Data",
    },
)


class OpenRouterService:
    def __init__(self, model_name: str = None):
        self.model_name = model_name or getattr(
            settings,
            "OPENROUTER_MODEL",
            "meta-llama/llama-3.3-70b-instruct:free",
        )

    def generate(self, prompt: str, system_instruction: str = None, temperature: float = 0.0) -> str:
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        response = _client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=2048,
        )
        return response.choices[0].message.content.strip()

    def generate_json(self, prompt: str, system_instruction: str = None) -> str:
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        response = _client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.0,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content.strip()


openrouter_service = OpenRouterService()