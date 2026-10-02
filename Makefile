.PHONY: api client integration-test ui-check ui-browser-install ui-staging-qa ui-repair-check ui-autofix ui-smart-tests ui-visual-update

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

ui-repair-check:
	bash scripts/cam-ui-repair-check.sh

ui-autofix:
	bash scripts/cam-ui-autofix.sh

ui-smart-tests:
	bash scripts/cam-ui-run-smart-tests.sh

ui-visual-update:
	CAM_UI_VISUAL_UPDATE=1 bash scripts/cam-ui-staging-qa.sh
