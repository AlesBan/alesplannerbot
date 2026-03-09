from openai import OpenAI

from app.config import get_settings


class OpenAIClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else None

    def chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        if not self.client:
            return "AI is disabled. Add OPENAI_API_KEY to enable planning suggestions."

        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""
