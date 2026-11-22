"""App assets revalidate on every load; only vendored libraries are cached long."""

from fastapi.testclient import TestClient

from rahat.api import app


def test_app_assets_revalidate_and_vendor_is_cached():
    with TestClient(app) as c:
        r = c.get("/static/wall.js")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers.get("etag")

        # Unchanged file: the browser's revalidation costs a 304, not a download.
        again = c.get("/static/wall.js", headers={"If-None-Match": r.headers["etag"]})
        assert again.status_code == 304

        assert c.get("/static/wall.css").headers["cache-control"] == "no-cache"
        assert c.get("/wall").headers["cache-control"] == "no-cache"

        v = c.get("/static/vendor/leaflet/leaflet.js")
        assert v.status_code == 200
        assert "max-age=31536000" in v.headers["cache-control"]
