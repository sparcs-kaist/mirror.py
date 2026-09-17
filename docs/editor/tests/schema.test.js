import assert from 'node:assert/strict';
import test from 'node:test';
import { createDefaultConfig, createPackage, validateConfig } from '../src/schema.js';

test('malformed method names and repository selections yield diagnostics, not exceptions', () => {
  const config = createDefaultConfig();
  config.packages.sample = createPackage('sample', 'apt-mirror2');
  config.packages.sample.settings.options.config = [{ src: 'https://example.org', dst: 'repo', check_gpg: false, dist: [null, 1, {}] }];
  assert.ok(validateConfig(config).some((issue) => issue.severity === 'error'));
  config.packages.sample.synctype = { toString: 1 };
  assert.ok(validateConfig(config).some((issue) => issue.severity === 'error'));
});
