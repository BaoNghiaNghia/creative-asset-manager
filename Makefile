.PHONY: api client integration-test ui-check ui-browser-install ui-staging-qa ui-visual-update

api:
	bash scripts/dev-api.sh

client:
	cd apps/client && npm install && npm run dev

integration-test:
	bash scripts/test-integration.sh

ui-check:
	bash scripts/cam-ui-gate.sh

ui-browser-install:
	bash scripts/cam-install-ui-browser.sh

ui-staging-qa:
	bash scripts/cam-ui-staging-qa.sh

ui-visual-update:
	CAM_UI_VISUAL_UPDATE=1 bash scripts/cam-ui-staging-qa.sh
