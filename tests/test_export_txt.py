import app


def test_export_downloads_plain_text(tmp_path):
    app.app.config["TESTING"] = True
    client = app.app.test_client()

    text_body = "first page\n[newpage]\nsecond page"

    resp = client.post("/export", data={"text": text_body, "writing_mode": "horizontal"})

    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/plain")
    assert "export.txt" in resp.headers.get("Content-Disposition", "")
    assert resp.get_data(as_text=True) == text_body
