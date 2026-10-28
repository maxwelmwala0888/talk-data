"""
Grok (xAI) LLM service — OpenAI-compatible API at https://api.x.ai/v1.
Privacy: xAI never trains on API inputs/outputs.
"""

from openai import OpenAI
from app.core.config import settings

_client = OpenAI(
    api_key=getattr(settings, "XAI_API_KEY", "") or "",
    base_url="https://api.x.ai/v1",
)


class GrokService:
    def __init__(self, model_name: str = None):
        self.model_name = model_name or getattr(settings, "XAI_MODEL", "grok-4.3")

    def generate(self, prompt: str, system_instruction: str = None, temperature: float = 0.0) -> str:
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        response = _client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=4096,
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
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content.strip()


grok_service = GrokService()