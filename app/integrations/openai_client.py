from openai import OpenAI

from app.config import get_settings


class OpenAIClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.provider = "disabled"
        self.model = settings.openai_model
        self.client = None

        if settings.deepseek_api_key:
            self.provider = "deepseek"
            self.model = settings.deepseek_model
            self.client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
        elif settings.openai_api_key:
            self.provider = "openai"
            self.model = settings.openai_model
            self.client = OpenAI(api_key=settings.openai_api_key)

    def chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        if not self.client:
            return "AI is disabled. Add DEEPSEEK_API_KEY or OPENAI_API_KEY."
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            error_text = str(exc)
            if "unsupported_country_region_territory" in error_text:
                return "AI unavailable: unsupported country/region for this API key."
            return f"AI unavailable: {error_text}"

    def chat_with_messages(self, messages: list[dict], temperature: float = 0.3) -> str:
        if not self.client:
            return "AI is disabled. Add DEEPSEEK_API_KEY or OPENAI_API_KEY."
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                messages=messages,
            )
            return response.choices[0].message.content or ""
        except Exception as exc:
            error_text = str(exc)
            if "unsupported_country_region_territory" in error_text:
                return "AI unavailable: unsupported country/region for this API key."
            return f"AI unavailable: {error_text}"
