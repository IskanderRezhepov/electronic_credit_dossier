
from pathlib import Path
from flask import Flask


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    app.config.from_mapping(
        SECRET_KEY="change-this-in-production",
        MAX_CONTENT_LENGTH=80 * 1024 * 1024,
        RESULT_FOLDER=str(Path(app.instance_path) / "results"),
        OCR_LANGUAGES="rus+kaz+eng",
        OCR_DPI=220,
        MIN_DIGITAL_TEXT_CHARS=80,
    )

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["RESULT_FOLDER"]).mkdir(parents=True, exist_ok=True)

    from .routes import bp
    app.register_blueprint(bp)

    return app
