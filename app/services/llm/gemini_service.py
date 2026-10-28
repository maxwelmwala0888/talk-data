import google.generativeai as genai
from app.core.config import settings

genai.configure(api_key=settings.GEMINI_API_KEY)


class GeminiService:
    def __init__(self, model_name: str = None):
        self.model_name = model_name or settings.GEMINI_MODEL
        self.model = genai.GenerativeModel(self.model_name)

    def generate(self, prompt: str, system_instruction: str = None, temperature: float = 0.0) -> str:
        full_prompt = prompt
        if system_instruction:
            full_prompt = f"{system_instruction}\n\n{prompt}"
        response = self.model.generate_content(
            full_prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=temperature,
                max_output_tokens=2048,
            ),
        )
        return response.text.strip()

    def generate_json(self, prompt: str, system_instruction: str = None) -> str:
        response = self.model.generate_content(
            f"{system_instruction}\n\n{prompt}" if system_instruction else prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        return response.text.strip()


gemini_service = GeminiService()