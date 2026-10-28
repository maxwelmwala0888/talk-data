from groq import Groq
from app.core.config import settings

_client = Groq(api_key=getattr(settings, "GROQ_API_KEY", "") or "")


class GroqService:
    def __init__(self, model_name: str = None):
        self.model_name = model_name or getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")

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


groq_service = GroqService()