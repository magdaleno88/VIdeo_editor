import unicodedata
from typing import Protocol

from app.schemas.domain import Idea


class QueryGenerator(Protocol):
    def generate(self, idea: Idea) -> list[str]: ...


def normalize_term(term: str) -> str:
    plain = unicodedata.normalize("NFKD", term.casefold())
    return " ".join("".join(c for c in plain if not unicodedata.combining(c)).split())


# A small, explicit bilingual vocabulary, not a general translation engine.
TRANSLATIONS = {
    "tornillo": "screw",
    "tornillos": "screw",
    "clavo": "nail",
    "clavos": "nail",
    "rodamiento": "bearing",
    "rodamientos": "bearing",
    "prensa": "press",
    "prensa industrial": "industrial press",
    "botella": "bottle",
    "botella de vidrio": "glass bottle",
    "botellas de vidrio": "glass bottle",
    "cadena": "chain",
    "cadenas": "chain",
    "engranaje": "gear",
    "tuerca": "nut",
    "fundicion": "casting",
    "forjado": "forging",
    "estampado": "stamping",
    "soldadura": "welding",
    "extrusion": "extrusion",
    "moldeo por inyeccion": "injection molding",
    "laminado de roscas": "thread rolling",
    "cabeceado en frio": "cold heading",
    "mecanizado": "machining",
    "metal": "metal",
    "vidrio": "glass",
    "plastico": "plastic",
    "metales": "metal",
    "fabricacion de tornillos": "screw manufacturing",
}
PROCESS_HINTS = {
    "screw": ["cold heading screw machine", "thread rolling machine"],
    "nail": ["wire nail making machine", "nail production line"],
    "bearing": ["bearing grinding machine", "bearing assembly line"],
    "glass bottle": ["glass bottle blow molding", "glass bottle forming machine"],
    "chain": ["chain link welding machine"],
    "gear": ["gear hobbing machine"],
}


class TemplateQueryGenerator:
    def __init__(self, translations: dict[str, str] | None = None) -> None:
        self.translations = TRANSLATIONS | {
            normalize_term(k): v for k, v in (translations or {}).items()
        }

    def translate(self, term: str) -> str:
        normalized = normalize_term(term)
        return self.translations.get(normalized, normalized)

    def generate(self, idea: Idea) -> list[str]:
        obj = self.translate(idea.object_name)
        queries = [f"how {obj} is made", f"{obj} manufacturing process", f"{obj} production line"]
        if idea.process_name:
            process = self.translate(idea.process_name)
            queries[0:0] = [
                f"{obj} {process}",
                f"{process} machine",
                f"{process} industrial process",
            ]
        queries.extend(PROCESS_HINTS.get(obj, []))
        queries.extend(
            [f"{obj} factory", f"industrial {obj} production", f"{obj} manufacturing machine"]
        )
        if idea.category:
            queries.insert(0, f"{self.translate(idea.category)} {obj} manufacturing")
        # Both providers support these lengths; never silently truncate a query.
        return list(dict.fromkeys(q for q in queries if len(q) <= 100))
