"""Compatibility entry point for POLO Documentation & File Center."""
from docs_only import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
