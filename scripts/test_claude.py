"""Test rapide : la clé Claude marche-t-elle via le SDK ?"""

import asyncio

from anthropic import AsyncAnthropic

from app.config import get_settings


async def main():
    get_settings.cache_clear()
    s = get_settings()
    print(f"Key length: {len(s.anthropic_api_key)}")
    print(f"Key tail: ...{s.anthropic_api_key[-8:]}")
    print(f"Model: {s.claude_model_default}")
    client = AsyncAnthropic(api_key=s.anthropic_api_key)
    r = await client.messages.create(
        model=s.claude_model_default,
        max_tokens=50,
        messages=[{"role": "user", "content": "OK"}],
    )
    print(f"\nClaude says: {r.content[0].text[:100]}")


if __name__ == "__main__":
    asyncio.run(main())
