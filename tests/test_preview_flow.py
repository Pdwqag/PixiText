import app


def test_preview_requires_session_text_redirects_home():
    app.app.config["TESTING"] = True
    client = app.app.test_client()

    resp = client.get("/preview")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_preview_api_returns_expected_page_slice():
    app.app.config["TESTING"] = True
    client = app.app.test_client()

    with client.session_transaction() as sess:
        sess["last_text"] = "first page\n[newpage]\nsecond page"
        sess["last_writing_mode"] = "vertical"

    resp = client.get("/api/preview_page?p=2")
    data = resp.get_json()

    assert resp.status_code == 200
    assert data["success"] is True
    assert data["p"] == 2
    assert data["total"] == 2
    assert "second page" in data["page_html"]
    assert data["writing_mode"] == "vertical"
