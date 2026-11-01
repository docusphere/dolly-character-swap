import character_swap


def test_package_has_version():
    assert character_swap.__version__ == "0.1.0"


def test_web_stack_is_installed():
    import jinja2
    import uvicorn
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    assert TestClient(app).get("/ping").json() == {"ok": True}
    assert jinja2.__version__ and uvicorn.__version__
