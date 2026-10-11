"""Category/basis invariants across catalog writes and frozen algorithm inputs."""

import hashlib
import json
from dataclasses import replace

import pytest

from backend.algorithm.snapshot_adapter import AlgorithmInputError, build_algorithm_input
from backend.auth.principal import Principal
from backend.domain.catalog import AircraftResourceRequirement, AircraftType, CatalogValidationError, ResourceType
from backend.storage.airport_repository import AirportRepository
from backend.storage.mission_repository import MissionRepository
from backend.web.catalog_api import CatalogApi
from tests.algorithm.test_snapshot_adapter import make_snapshot


CASES = [(category, basis, basis == expected)
         for category, expected in (("fuel", "per_hour"), ("material", "per_sortie"), ("munition", "per_sortie"))
         for basis in ("per_hour", "per_sortie")]


@pytest.fixture
def catalog(tmp_path):
    repo = AirportRepository(tmp_path / "catalog.sqlite3")
    repo.init_schema()
    repo.save_aircraft_type(AircraftType("fighter", "Fighter"))
    for category in ("fuel", "material", "munition"):
        repo.save_resource_type(ResourceType(category, category, category, "t"))
    api = CatalogApi(airport_repository=repo, mission_repository=MissionRepository(repo.db_path))
    return repo, api, Principal("ADMIN", is_admin=True)


@pytest.mark.parametrize("category,basis,valid", CASES)
def test_requirement_validates_against_resource_without_storing_category(category, basis, valid):
    row = AircraftResourceRequirement("fighter", category, basis, 1.5)
    resource = ResourceType(category, category, category, "t")
    assert "category" not in row.to_dict()
    if valid:
        row.validate_resource(resource)
    else:
        with pytest.raises(CatalogValidationError) as error:
            row.validate_resource(resource)
        assert error.value.field == "basis"
        assert category in str(error.value)


@pytest.mark.parametrize("category,basis,valid", CASES)
@pytest.mark.parametrize("writer", ["single", "aircraft", "bulk"])
def test_repository_write_paths_enforce_basis_atomically(catalog, category, basis, valid, writer):
    repo, _, _ = catalog
    expected = "per_hour" if category == "fuel" else "per_sortie"
    original = AircraftResourceRequirement("fighter", category, expected, 2)
    repo.save_aircraft_resource_requirement(original)
    row = AircraftResourceRequirement("fighter", category, basis, 1.5)
    revision = repo.list_aircraft_types_with_metadata()[0]["metadata"]["revision"]

    def write():
        if writer == "single":
            repo.save_aircraft_resource_requirement(row)
        elif writer == "aircraft":
            repo.replace_aircraft_resource_requirements("fighter", [row], expected_aircraft_revision=revision)
        else:
            repo.replace_aircraft_resource_requirements_current([row])

    if valid:
        write()
        assert repo.list_aircraft_resource_requirements() == [row]
    else:
        with pytest.raises(CatalogValidationError):
            write()
        assert repo.list_aircraft_resource_requirements() == [original]
        assert repo.list_aircraft_types_with_metadata()[0]["metadata"]["revision"] == revision


@pytest.mark.parametrize("category,basis,valid", CASES)
@pytest.mark.parametrize("bulk", [False, True])
def test_catalog_api_and_import_reject_invalid_basis_with_row_field(catalog, category, basis, valid, bulk):
    repo, api, admin = catalog
    row = AircraftResourceRequirement("fighter", category, basis, 1.5).to_dict()
    if bulk:
        response = api.replace_base_data_json({"dataset": "aircraft_resource_requirements", "items": [row]}, principal=admin)
    else:
        response = api.replace_aircraft_resource_requirements(
            "fighter", {"requirements": [row], "expected_revision": 1}, principal=admin)
    assert response.status == (200 if valid else 422), response.body
    if valid:
        assert repo.list_aircraft_resource_requirements()[0].to_dict() == row
    else:
        assert response.body["error"]["code"] == "VALIDATION_FAILED"
        assert response.body["error"]["field"] == ("items[0].basis" if bulk else "requirements[0].basis")
        assert category in response.body["error"]["message"]
        assert repo.list_aircraft_resource_requirements() == []


@pytest.mark.parametrize("writer", ["single", "versioned", "bulk"])
@pytest.mark.parametrize("old,new", [("fuel", "material"), ("material", "fuel"), ("munition", "fuel")])
def test_category_change_cannot_invalidate_existing_requirement(catalog, writer, old, new):
    repo, _, _ = catalog
    basis = "per_hour" if old == "fuel" else "per_sortie"
    row = AircraftResourceRequirement("fighter", old, basis, 1)
    repo.save_aircraft_resource_requirement(row)
    changed = ResourceType(old, old, new, "t")
    with pytest.raises(CatalogValidationError) as error:
        if writer == "single":
            repo.save_resource_type(changed)
        elif writer == "versioned":
            repo.save_resource_type_versioned(changed, expected_revision=1)
        else:
            repo.replace_resource_types_current([changed if item.resource_type_id == old else item for item in repo.list_resource_types()])
    assert error.value.field == "category"
    assert next(item for item in repo.list_resource_types() if item.resource_type_id == old).category == old
    assert repo.list_aircraft_resource_requirements() == [row]


@pytest.mark.parametrize("category,basis,valid", CASES)
def test_snapshot_boundary_rejects_invalid_category_basis(category, basis, valid):
    snapshot = make_snapshot()
    payload = snapshot.to_dict()
    catalogs = payload["catalogs"]
    catalogs["resource_types"][1]["category"] = category
    catalogs["aircraft_resource_requirements"][1]["basis"] = basis
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    frozen = replace(snapshot, payload_json=text, content_hash=hashlib.sha256(text.encode()).hexdigest())
    if valid:
        bundle = build_algorithm_input(frozen)
        assert bundle.run_params["aircrafts"]["fighter"]["resource_requirements"]["MAT-1"]["basis"] == basis
        if category != "fuel":
            assert bundle.run_params["aircrafts"]["fighter"]["fuel_rate"] == 1.5
    else:
        with pytest.raises(AlgorithmInputError, match=f"{category}.*{('per_hour' if category == 'fuel' else 'per_sortie')}"):
            build_algorithm_input(frozen)


def test_snapshot_rejects_illegal_zero_quantity_row_even_if_valid_row_has_same_resource():
    snapshot = make_snapshot()
    payload = snapshot.to_dict()
    payload["catalogs"]["aircraft_resource_requirements"].insert(0, {
        "aircraft_type_id": "fighter", "resource_type_id": "FUEL-A", "basis": "per_sortie", "quantity": 0,
    })
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    frozen = replace(snapshot, payload_json=text, content_hash=hashlib.sha256(text.encode()).hexdigest())
    with pytest.raises(AlgorithmInputError, match="fuel.*per_hour"):
        build_algorithm_input(frozen)


@pytest.mark.parametrize("bulk", [False, True])
def test_api_resource_category_change_returns_locatable_error(catalog, bulk):
    repo, api, admin = catalog
    repo.save_aircraft_resource_requirement(AircraftResourceRequirement("fighter", "fuel", "per_hour", 1))
    changed = ResourceType("fuel", "Fuel", "material", "t").to_dict()
    if bulk:
        items = [changed if item.resource_type_id == "fuel" else item.to_dict() for item in repo.list_resource_types()]
        response = api.replace_base_data_json({"dataset": "resource_types", "items": items}, principal=admin)
    else:
        response = api.update_resource_type("fuel", {"resource_type": changed, "expected_revision": 1}, principal=admin)
    assert response.status == 422
    assert response.body["error"]["code"] == "VALIDATION_FAILED"
    assert response.body["error"]["field"] == "category"
    assert next(item for item in repo.list_resource_types() if item.resource_type_id == "fuel").category == "fuel"


def test_standard_demo_catalog_creation_uses_unchanged_consumption_parameters(tmp_path):
    from tools.dev_validation.build_demo_workspace import AIRCRAFT_RESOURCE_REQUIREMENTS, prepare_catalogs

    repo = AirportRepository(tmp_path / "demo.sqlite3")
    repo.init_schema()
    api = CatalogApi(airport_repository=repo, mission_repository=MissionRepository(repo.db_path))
    admin = Principal("ADMIN", is_admin=True)

    class DemoTestClient:
        def request(self, method, path, *, body=None, expected=(200,)):
            if path == "/api/resource-types":
                response = (api.list_resource_types(principal=admin) if method == "GET"
                            else api.create_resource_type(body, principal=admin))
            elif path == "/api/aircraft-types":
                response = (api.list_aircraft_types(principal=admin) if method == "GET"
                            else api.create_aircraft_type(body, principal=admin))
            elif path == "/api/aircraft-resource-requirements":
                response = api.list_aircraft_resource_requirements(principal=admin)
            elif path.endswith("/resource-requirements"):
                response = api.replace_aircraft_resource_requirements(path.split("/")[-2], body, principal=admin)
            else:
                raise AssertionError(f"Unexpected demo request: {method} {path}")
            assert response.status in expected, response.body
            return response.body

    prepare_catalogs(DemoTestClient())
    rows = repo.list_aircraft_resource_requirements()
    assert len(rows) == sum(len(items) for items in AIRCRAFT_RESOURCE_REQUIREMENTS.values())
    for aircraft_id, definitions in AIRCRAFT_RESOURCE_REQUIREMENTS.items():
        assert {(row.resource_type_id, row.basis, row.quantity) for row in rows if row.aircraft_type_id == aircraft_id} == set(definitions)
    prepare_catalogs(DemoTestClient())  # Existing valid standard catalogs remain reusable.
    assert repo.list_aircraft_resource_requirements() == rows
