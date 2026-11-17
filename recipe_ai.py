import os
import json
import re

from openai import APIConnectionError, APIStatusError, OpenAI, OpenAIError
from pydantic import BaseModel, Field, ValidationError


DEFAULT_MODEL = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "gpt-5.6")
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
MAX_TRANSCRIPT_CHARS = int(os.getenv("MAX_TRANSCRIPT_CHARS", "20000"))


class RecipeExtractionError(RuntimeError):
    pass


class Ingredient(BaseModel):
    name: str
    quantity: float | None
    unit: str | None
    confidence: float = Field(ge=0, le=1)


class Recipe(BaseModel):
    recipe_name: str
    servings: int | None
    ingredients: list[Ingredient]


def is_ai_configured() -> bool:
    return bool(LLM_API_KEY)


def get_ai_config() -> dict[str, str | bool | None]:
    return {
        "configured": is_ai_configured(),
        "model": DEFAULT_MODEL,
        "base_url_configured": bool(LLM_BASE_URL),
        "mode": "openai_compatible_chat" if LLM_BASE_URL else "openai_responses",
    }


def build_openai_client(api_key: str | None = None, base_url: str | None = None) -> OpenAI:
    """Shared client factory so the recipe and audio paths stay configured alike."""
    key = api_key or LLM_API_KEY
    if not key:
        raise RecipeExtractionError("LLM_API_KEY or OPENAI_API_KEY is not configured.")

    if base_url:
        return OpenAI(api_key=key, base_url=base_url)
    return OpenAI(api_key=key)


def extract_recipe(transcript: str) -> Recipe:
    if not transcript or not transcript.strip():
        raise RecipeExtractionError("Transcript is empty.")

    if not is_ai_configured():
        raise RecipeExtractionError("LLM_API_KEY or OPENAI_API_KEY is not configured.")

    transcript = transcript.strip()
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS]

    if LLM_BASE_URL:
        return _extract_with_chat_completions(transcript)

    return _extract_with_openai_responses(transcript)


def _extract_with_openai_responses(transcript: str) -> Recipe:
    client = build_openai_client()

    try:
        response = client.responses.parse(
            model=DEFAULT_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You extract structured recipes from cooking-video transcripts. "
                        "Return only ingredients that are actually used in the recipe."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_prompt(transcript),
                },
            ],
            text_format=Recipe,
            max_output_tokens=1800,
            store=False,
        )
    except APIConnectionError as exc:
        raise RecipeExtractionError("Could not connect to OpenAI.") from exc
    except APIStatusError as exc:
        raise RecipeExtractionError(
            f"OpenAI request failed with status {exc.status_code}."
        ) from exc
    except OpenAIError as exc:
        raise RecipeExtractionError(f"OpenAI request failed: {exc}") from exc

    if response.output_parsed is None:
        raise RecipeExtractionError("OpenAI did not return a parsed recipe.")

    return response.output_parsed


def _extract_with_chat_completions(transcript: str) -> Recipe:
    client = build_openai_client(base_url=LLM_BASE_URL)

    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You extract structured recipes from cooking-video transcripts. "
                        "Return JSON only. Do not include markdown, reasoning, or explanations."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_json_prompt(transcript),
                },
            ],
            temperature=0,
            max_completion_tokens=1800,
            response_format={"type": "json_object"},
        )
    except APIConnectionError as exc:
        raise RecipeExtractionError("Could not connect to the configured LLM.") from exc
    except APIStatusError as exc:
        raise RecipeExtractionError(
            f"Configured LLM request failed with status {exc.status_code}."
        ) from exc
    except OpenAIError as exc:
        raise RecipeExtractionError(f"Configured LLM request failed: {exc}") from exc

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise RecipeExtractionError("Configured LLM did not return recipe JSON.")

    try:
        return _parse_recipe_json(content)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise RecipeExtractionError("Configured LLM returned invalid recipe JSON.") from exc


def _build_prompt(transcript: str) -> str:
    return (
        "Read this cooking-video transcript and extract the recipe.\n\n"
        "Rules:\n"
        "- Extract only food ingredients actually used in the recipe.\n"
        "- Use canonical grocery names, for example 'cumin seeds' instead of 'jeera'.\n"
        "- Include quantity and unit only when the transcript supports them.\n"
        "- If quantity or unit is unavailable, return null for that field.\n"
        "- Do not include cooking instructions, utensils, cookware, garnishes only shown as examples, or alternatives that are not used.\n"
        "- Confidence must be between 0 and 1.\n\n"
        f"Transcript:\n{transcript}"
    )


def _build_json_prompt(transcript: str) -> str:
    return (
        f"{_build_prompt(transcript)}\n\n"
        "Return exactly one JSON object with this shape:\n"
        "{\n"
        '  "recipe_name": "string",\n'
        '  "servings": 2,\n'
        '  "ingredients": [\n'
        "    {\n"
        '      "name": "canonical grocery ingredient name",\n'
        '      "quantity": 1.5,\n'
        '      "unit": "g",\n'
        '      "confidence": 0.95\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Use null for unknown servings, quantity, or unit."
    )


def _parse_recipe_json(content: str) -> Recipe:
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE)
    cleaned = cleaned.strip()

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found.")

    payload = json.loads(cleaned[start:end + 1])
    return Recipe.model_validate(payload)
