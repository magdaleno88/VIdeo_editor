import re

TECHNICAL_PAIRS = {
    "thread rolling",
    "cold heading",
    "hot forging",
    "acero inoxidable",
    "tratamiento térmico",
}


def caption_tokens(text: str) -> list[str]:
    tokens = re.findall(
        r"\d[\d.,]*\s*°?\s*(?:[A-Za-zÀ-ÿ%]+)?|[A-Za-zÀ-ÿ]+(?:[-/][A-Za-zÀ-ÿ]+)*|[^\w\s]",
        " ".join(text.split()),
        flags=re.UNICODE,
    )
    merged: list[str] = []
    index = 0
    while index < len(tokens):
        if index + 1 < len(tokens):
            pair = f"{tokens[index]} {tokens[index + 1]}".lower()
            if pair in TECHNICAL_PAIRS:
                merged.append(f"{tokens[index]} {tokens[index + 1]}")
                index += 2
                continue
        merged.append(tokens[index])
        index += 1
    return merged


def join_tokens(tokens: list[str]) -> str:
    text = " ".join(tokens)
    return re.sub(r"\s+([,.;:!?])", r"\1", text).strip()


def segment_caption(text: str, min_words: int, max_words: int, max_characters: int) -> list[str]:
    tokens = caption_tokens(text)
    chunks: list[list[str]] = []
    current: list[str] = []
    words = 0
    for token in tokens:
        token_words = max(1, len(token.split())) if token not in ",.;:!?" else 0
        candidate = join_tokens([*current, token])
        if current and (words + token_words > max_words or len(candidate) > max_characters):
            chunks.append(current)
            current = []
            words = 0
        current.append(token)
        words += token_words
        if token in ".;!?" and words >= 2:
            chunks.append(current)
            current = []
            words = 0
    if current:
        chunks.append(current)
    values = [join_tokens(chunk) for chunk in chunks if join_tokens(chunk)]
    index = len(values) - 1
    while index > 0:
        if len(values[index].split()) < min_words:
            combined = f"{values[index - 1]} {values[index]}"
            if len(combined) <= max_characters and len(combined.split()) <= max_words:
                values[index - 1 : index + 1] = [combined]
        index -= 1
    return values
