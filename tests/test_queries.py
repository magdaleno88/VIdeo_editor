import pytest
from pydantic import ValidationError

from app.schemas.domain import DiscoveryRequest, Idea
from app.services.discovery.queries import TemplateQueryGenerator


@pytest.mark.parametrize(
    "term,expected",
    [
        ("tornillo", "screw manufacturing process"),
        ("clavo", "nail production line"),
        ("rodamientos", "bearing factory"),
        ("botella de vidrio", "glass bottle blow molding"),
        ("cadena", "chain link welding machine"),
        ("gear", "gear hobbing machine"),
    ],
)
def test_multiple_industrial_objects(term, expected):
    queries = TemplateQueryGenerator().generate(Idea(object_name=term))
    assert expected in queries
    assert len(queries) >= 6
    assert len(queries) == len(set(queries))
    assert all(len(query) <= 100 for query in queries)


def test_process_category_and_extension():
    generator = TemplateQueryGenerator({"pieza especial": "custom part"})
    queries = generator.generate(
        Idea(object_name="Pieza especial", process_name="fundición", category="metales")
    )
    assert "custom part casting" in queries
    assert "metal custom part manufacturing" in queries
    assert "casting machine" in queries
    assert "metal custom part manufacturing" in queries[:3]
    assert "custom part casting" in queries[:3]


def test_unknown_term_preserved_without_claiming_translation():
    assert "sprocket factory" in TemplateQueryGenerator().generate(Idea(object_name="sprocket"))


@pytest.mark.parametrize(
    "body",
    [
        {"object_name": " "},
        {"object_name": "x", "max_queries": 9},
        {"object_name": "x", "per_page": 1},
        {"object_name": "x", "providers": []},
        {"object_name": "x", "providers": ["youtube"]},
        {"object_name": "x", "page": 0},
    ],
)
def test_bounded_discovery_request(body):
    with pytest.raises(ValidationError):
        DiscoveryRequest(**body)
