#!/usr/bin/env node
// Lance le serveur MCP Chrome DevTools (version figée).
// Dans un conteneur Claude Code (cloud), utilise le Chromium préinstallé en mode headless ;
// sur un ordinateur, utilise le Chrome installé, avec un profil isolé.
const { spawn } = require('child_process');
const fs = require('fs');

const VERSION = '1.12.0';
const CLOUD_CHROMIUM = '/opt/pw-browsers/chromium';

const args = ['-y', `chrome-devtools-mcp@${VERSION}`, '--isolated'];
if (fs.existsSync(CLOUD_CHROMIUM)) {
  args.push('--headless', `--executablePath=${CLOUD_CHROMIUM}`, '--chromeArg=--no-sandbox');
}
args.push(...process.argv.slice(2));

const npx = process.platform === 'win32' ? 'npx.cmd' : 'npx';
const child = spawn(npx, args, { stdio: 'inherit', shell: process.platform === 'win32' });
child.on('exit', code => process.exit(code ?? 0));
