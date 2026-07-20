.PHONY: api client integration-test

api:
	bash scripts/dev-api.sh

client:
	cd apps/client && npm install && npm run dev

integration-test:
	bash scripts/test-integration.sh
