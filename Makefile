.PHONY: api client

api:
	bash scripts/dev-api.sh

client:
	cd apps/client && npm install && npm run dev
