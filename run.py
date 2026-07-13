"""BubbleEvent - Hot Event Driven Investment Agent System."""
import os
# Force offline mode for HuggingFace Hub — model is already cached locally.
# Prevents SSL errors and 5× ~10s retries when huggingface.co is unreachable.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
