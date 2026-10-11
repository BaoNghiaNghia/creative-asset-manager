.PHONY: api client integration-test ui-check ui-browser-install ui-staging-qa ui-profile-matrix ui-qa-headless ui-qa-visual ui-repair-check ui-autofix ui-smart-tests ui-visual-propose ui-visual-accept ui-visual-update production-ui-smoke

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

ui-profile-matrix:
	bash scripts/cam-ui-profile-matrix.sh

# One-command, no-login UI regression checks. Fixture-only, headless, strict.
# Defaults to all workspaces; override CAM_UI_QA_PROFILES and CAM_UI_VIEWPORTS.
ui-qa-headless:
	@CAM_UI_QA_PROFILES="$${CAM_UI_QA_PROFILES:-explorer-viewer,review-board,realistic-review-ugc,ai-operations,inventory,access-management,video-generation,job-queue,public-review}" \
	 CAM_UI_VIEWPORTS="$${CAM_UI_VIEWPORTS:-desktop,tabletPortrait,mobile}" \
	 CAM_UI_VISUAL_SKIP=1 CAM_UI_QA_STRICT=1 bash scripts/cam-ui-profile-matrix.sh

# Separate visual regression audit; baseline changes require manual review.
ui-qa-visual:
	@CAM_UI_QA_PROFILES="$${CAM_UI_QA_PROFILES:-explorer-viewer,review-board,realistic-review-ugc,ai-operations,inventory,access-management,video-generation,job-queue,public-review}" \
	 CAM_UI_VIEWPORTS="$${CAM_UI_VIEWPORTS:-desktop,tabletPortrait,mobile}" \
	 CAM_UI_QA_STRICT=1 bash scripts/cam-ui-profile-matrix.sh

ui-repair-check:
	bash scripts/cam-ui-repair-check.sh

ui-autofix:
	bash scripts/cam-ui-autofix.sh

ui-smart-tests:
	bash scripts/cam-ui-run-smart-tests.sh

ui-visual-propose:
	bash scripts/cam-ui-baseline-propose.sh

ui-visual-accept:
	bash scripts/cam-ui-baseline-accept.sh

ui-visual-update:
	@echo "Direct baseline writes are disabled; creating a governed proposal instead."
	bash scripts/cam-ui-baseline-propose.sh

ui-design-audit:
	node apps/client/scripts/ui-qa-impeccable.mjs --mode "$${CAM_IMPECCABLE_MODE:-advisory}"

ai-qa-research-install:
	bash scripts/cam-install-ai-qa-research.sh

research-doctor:
	integrations/agent-reach/.venv/bin/python integrations/agent-reach/research_intelligence.py doctor

research-collect:
	integrations/agent-reach/.venv/bin/python integrations/agent-reach/research_intelligence.py collect

research-inbox:
	integrations/agent-reach/.venv/bin/python integrations/agent-reach/research_intelligence.py list

research-stage0-export:
	integrations/agent-reach/.venv/bin/python integrations/agent-reach/research_intelligence.py export-stage0

production-ui-smoke:
	bash scripts/cam-production-ui-smoke.sh
