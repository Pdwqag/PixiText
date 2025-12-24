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
    assert data["page_text"].strip() == "second page"
    assert data["writing_mode"] == "vertical"


def test_preview_displays_all_pages_inline():
    app.app.config["TESTING"] = True
    client = app.app.test_client()

    with client.session_transaction() as sess:
        sess["last_text"] = "first page\n[newpage]\nsecond page"
        sess["last_writing_mode"] = "horizontal"

    resp = client.get("/preview")
    body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert body.count("page-text__area") == 2
    assert "first page" in body
    assert "second page" in body


def test_preview_handles_parse_errors_gracefully(monkeypatch):
    app.app.config["TESTING"] = True
    client = app.app.test_client()

    with client.session_transaction() as sess:
        sess["last_text"] = "boom"

    monkeypatch.setattr("app.parse_document", lambda _text: (_ for _ in ()).throw(RuntimeError("explode")))

    resp = client.get("/preview")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_preview_api_reports_parse_errors(monkeypatch):
    app.app.config["TESTING"] = True
    client = app.app.test_client()

    with client.session_transaction() as sess:
        sess["last_text"] = "boom"

    monkeypatch.setattr("app.parse_document", lambda _text: (_ for _ in ()).throw(RuntimeError("explode")))

    resp = client.get("/api/preview_page?p=1")
    data = resp.get_json()

    assert resp.status_code == 400
    assert data["success"] is False
    assert "失敗" in data["message"]


def test_preview_api_accepts_post_payload_and_sets_session(monkeypatch):
    app.app.config["TESTING"] = True
    client = app.app.test_client()

    monkeypatch.setattr(app, "parse_document", lambda text: [{"html": text}])

    resp = client.post(
        "/api/preview_page",
        data={"text": "hello", "writing_mode": "vertical", "p": 1},
    )

    data = resp.get_json()

    assert resp.status_code == 200
    assert data["success"] is True
    assert data["page_html"] == "hello"
    assert data["writing_mode"] == "vertical"

    # セッションに保存され、プレビューへの遷移に使えることを確認
    with client.session_transaction() as sess:
        assert sess["last_text"] == "hello"
        assert sess["last_writing_mode"] == "vertical"
