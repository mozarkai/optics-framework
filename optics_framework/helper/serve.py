import uvicorn
from optics_framework.common.expose_api import app

def run_uvicorn_server(host: str = "127.0.0.1", port: int = 8000):
        """Run the Optics Framework API server using uvicorn."""
        uvicorn.run(app, host=host, port=port)
