VENV := .venv
PY   := $(VENV)/bin/python

.PHONY: install run test smoke zip

install:
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -c backend/requirements.lock -e "backend[dev]"

run:
	$(PY) -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload

test:
	cd backend && ../$(PY) -m pytest

# Live model check on the sample packets (real API calls; needs .env)
smoke:
	cd backend && ../$(PY) -m scripts.live_extract

# Submission archive (excludes .env, .venv, caches and submission_notes/)
zip:
	rm -rf dist && mkdir dist
	zip -qr dist/knight-submission-review.zip . -x ".venv/*" "*__pycache__*" "*.pytest_cache*" "*.egg-info/*" ".env" "dist/*" "submission_notes/*"
	@echo "Wrote dist/knight-submission-review.zip"
