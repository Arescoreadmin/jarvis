.PHONY: run api setup token logs clean

run:
	python main.py

api:
	uvicorn api.server:app --host 0.0.0.0 --port $${JARVIS_API_PORT:-8765} --reload

setup:
	pip install -r requirements.txt
	mkdir -p data/vectors data/notes
	cp -n .env.example .env || true
	@echo "Fill in .env with your API keys, then run: make run"

token:
	python -c "from api.server import create_token; print(create_token())"

logs:
	tail -f data/jarvis.log

clean:
	rm -f data/memory.db data/tasks.db data/calendar_token.json data/gmail_token.json
	rm -rf data/vectors/__default_tenant
	@echo "Memory cleared."
