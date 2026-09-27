# Realistic Review UGC Browser Scout

This companion runtime runs on a user-controlled desktop or laptop with Chrome or Chromium. Pinterest profile data and cookies remain local.

Setup:
1. Create a Python virtual environment.
2. Install apps/rrugc_scout/requirements.txt.
3. Install the Playwright Chromium runtime, or pass --chrome-executable to use a local Chrome/Chromium binary.
4. Create a campaign in Realistic Review UGC and copy the one-time Scout command.

The first time, sign in to Pinterest manually using the persistent profile directory. The Scout does not automate login, solve CAPTCHA, hide automation, bypass source controls, or extract credentials.

Default behavior:
- opens a normal Pinterest search URL;
- performs only the campaign's bounded number of scroll batches;
- extracts visible /pin/ links and *.pinimg.com images;
- sends candidate metadata to CAM;
- when campaign auto-import is enabled, CAM performs bounded server-side image validation and Managed Google Drive upload.
