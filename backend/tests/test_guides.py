"""Trip guides (3.1): storage and the REST API. Offline: temp SQLite DB.

A guide is personal data, so the one thing that must never regress is that
every guide endpoint sits behind the app password."""

import pytest
from fastapi.testclient import TestClient

from app import main, storage
from app.models import GUIDE_MAX_BYTES


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    return TestClient(main.app)


def doc(title="Rome weekend", **extra):
    return {"version": 1, "title": title, "start": "2027-03-05", "end": "2027-03-08", "days": [], **extra}


# --- storage -----------------------------------------------------------------------

def test_save_replace_list_delete(temp_db):
    assert storage.list_guides() == []
    saved = storage.save_guide("rome-2027", doc())
    assert (saved.slug, saved.data["title"]) == ("rome-2027", "Rome weekend")
    assert storage.get_guide("rome-2027").data == doc()

    storage.save_guide("rome-2027", doc(title="Rome, again"))  # same slug: replaced, not duplicated
    [only] = storage.list_guides()
    assert only.data["title"] == "Rome, again"

    storage.save_guide("oslo-2027", doc(title="Oslo"))
    assert {g.slug for g in storage.list_guides()} == {"rome-2027", "oslo-2027"}
    assert storage.delete_guide("rome-2027") is True
    assert storage.delete_guide("rome-2027") is False
    assert [g.slug for g in storage.list_guides()] == ["oslo-2027"]


def test_document_round_trips_verbatim(temp_db):
    body = doc(title="טיול לרומא", places=[{"id": "caffe", "name": "Sant’Eustachio", "tags": ["☕", "£"]}],
               bookings=[{"title": "Hotel", "ref": "ABC123"}])
    storage.save_guide("rome", body)
    storage.reset_state()  # a fresh connection reads what was written, not a cached object
    assert storage.get_guide("rome").data == body


def test_unreadable_row_is_skipped(temp_db):
    storage.save_guide("ok", doc())
    storage._with_conn(lambda conn: (conn.execute(
        "INSERT INTO guides (slug, data, updated_at) VALUES ('broken', 'not json', '2027-01-01')"), conn.commit()))
    storage._invalidate_cache()
    assert [g.slug for g in storage.list_guides()] == ["ok"]


# --- API -----------------------------------------------------------------------------

def test_api_crud(client):
    assert client.get("/api/guides").json() == []
    res = client.put("/api/guides/rome-2027", json=doc())
    assert res.status_code == 200
    assert res.json()["slug"] == "rome-2027" and res.json()["data"] == doc()
    [listed] = client.get("/api/guides").json()
    assert listed["data"]["title"] == "Rome weekend" and listed["updated_at"]

    assert client.put("/api/guides/rome-2027", json=doc(title="Rome v2")).status_code == 200
    assert [g["data"]["title"] for g in client.get("/api/guides").json()] == ["Rome v2"]

    assert client.delete("/api/guides/rome-2027").status_code == 204
    assert client.delete("/api/guides/rome-2027").status_code == 404
    assert client.get("/api/guides").json() == []


@pytest.mark.parametrize("slug", ["Rome", "rome_2027", "-rome", "a" * 64, "rome 2027"])
def test_api_rejects_bad_slugs(client, slug):
    assert client.put(f"/api/guides/{slug}", json=doc()).status_code == 422


def test_api_validates_the_document(client):
    assert client.put("/api/guides/x", json={"days": []}).status_code == 422  # no title
    assert client.put("/api/guides/x", json=doc(title="   ")).status_code == 422
    assert client.put("/api/guides/x", json=["not", "an", "object"]).status_code == 422
    too_big = doc(notes="x" * GUIDE_MAX_BYTES)
    assert client.put("/api/guides/x", json=too_big).status_code == 413
    assert client.get("/api/guides").json() == []


def test_api_is_behind_the_app_password(temp_db, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    client = TestClient(main.app)
    storage.save_guide("rome", doc(bookings=[{"ref": "ABC123"}]))

    assert client.get("/api/guides").status_code == 401
    assert client.put("/api/guides/rome", json=doc()).status_code == 401
    assert client.delete("/api/guides/rome").status_code == 401
    assert client.get("/api/guides", headers={"Authorization": "Bearer wrong"}).status_code == 401

    ok = {"Authorization": "Bearer s3cret"}
    assert [g["slug"] for g in client.get("/api/guides", headers=ok).json()] == ["rome"]
    assert client.delete("/api/guides/rome", headers=ok).status_code == 204
