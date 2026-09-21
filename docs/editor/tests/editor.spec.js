import { expect, test } from '@playwright/test';

const BUILTIN_METHODS = [
  'apt-mirror2',
  'bandersnatch',
  'debmirror',
  'ftpsync',
  'jigdo',
  'lftp',
  'local',
  'rsync',
  'ubuntu',
];

const DEFAULT_CONFIG = {
  mirrorname: 'My Mirror',
  hostname: 'mirror.example.com',
  settings: {
    logfolder: '/var/log/mirror/ftpsync',
    webroot: '/var/www/mirror',
    statusfile: '/var/www/mirror/status.json',
    statfile: '/var/lib/mirror/stat.json',
    uid: 0,
    gid: 0,
    localtimezone: 'UTC',
    errorcontinuetime: 60,
    maintainer: { name: 'Mirror Admin', email: 'admin@example.com' },
    logger: {
      level: 'INFO',
      packagelevel: 'INFO',
      format: '[%(asctime)s] %(levelname)s # %(message)s',
      packageformat: '[%(asctime)s][{package}] %(levelname)s # %(message)s',
      fileformat: {
        base: '/var/log/mirror',
        folder: '{year}/{month}',
        filename: '{year}-{month}-{day}.log',
        gzip: true,
      },
      packagefileformat: {
        base: '/var/log/mirror/packages',
        folder: '{year}/{month}/{day}',
        filename: '{packageid}.{hour}:{minute}:{second}.{microsecond}.log',
        gzip: true,
      },
    },
    ftpsync: {
      maintainer: 'Admins <admins@example.com>',
      sponsor: 'Example <https://example.com>',
      country: 'KR',
      location: 'Seoul',
      throughput: '1G',
    },
  },
  packages: {},
};

function packageConfig(id, synctype = 'rsync') {
  return {
    id,
    name: `Package ${id}`,
    href: `/${id}`,
    synctype,
    syncrate: 'PT6H',
    link: [{ rel: 'HOME', href: 'https://example.com/' }],
    settings: {
      hidden: false,
      src: 'rsync://example.com/repository',
      dst: `/srv/mirror/${id}`,
      options: {},
    },
  };
}

function configWithPackages(packages) {
  return structuredClone({ ...DEFAULT_CONFIG, packages });
}

function field(page, path) {
  return page.locator(`[data-path='${JSON.stringify(path)}']`).filter({ visible: true });
}

async function openEditor(page) {
  await page.goto('guide/config-editor.html');
  await expect(page.locator('#mirror-config-editor')).toBeVisible();
  await expect(page.locator('.monaco-editor')).toBeVisible();
  await expect(page.locator('#ce-status')).toContainText(/Valid configuration|0 errors/i);
}

async function importConfig(page, value) {
  await page.locator('#ce-file-input').setInputFiles({
    name: 'config.json',
    mimeType: 'application/json',
    buffer: Buffer.from(typeof value === 'string' ? value : JSON.stringify(value, null, 2)),
  });
}

async function selectPackage(page, packageId) {
  await page.getByLabel('Configuration section').selectOption(packageId);
  await page.getByRole('tab', { name: 'Sync', exact: true }).click();
  await expect(field(page, ['packages', packageId, 'synctype'])).toBeVisible();
}

async function fillFormField(page, path, value) {
  const input = field(page, path);
  await input.fill(value);
  await input.press('Tab');
}

async function replaceEditorText(page, text) {
  const input = page.locator('.monaco-editor textarea').first();
  await page.evaluate((value) => navigator.clipboard.writeText(value), text);
  await input.focus();
  await page.keyboard.press('ControlOrMeta+A');
  await page.keyboard.press('ControlOrMeta+V');
}

async function downloadText(page) {
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Download config.json' }).click(),
  ]);
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}

test.beforeEach(async ({ page }) => {
  await openEditor(page);
});

test('loads the editor and form from a nested documentation URL', async ({ page }) => {
  await expect(page).toHaveURL(/\/html\/guide\/config-editor\.html$/);
  await expect(page.locator('#ce-form')).toBeVisible();

  for (const name of [
    'New configuration',
    'Open file',
    'Format JSON',
    'Copy JSON',
    'Download config.json',
  ]) {
    await expect(page.getByRole('button', { name })).toBeVisible();
  }
  await expect(page.locator('#ce-file-input')).toBeAttached();
});

test('imports a configuration and reflects JSON values in the form', async ({ page }) => {
  const config = configWithPackages({ sample: packageConfig('sample') });
  config.mirrorname = 'Imported Mirror';

  await importConfig(page, config);

  await expect(field(page, ['mirrorname'])).toHaveValue('Imported Mirror');
  await expect(field(page, ['hostname'])).toHaveValue('mirror.example.com');
  await selectPackage(page, 'sample');
  await expect(field(page, ['packages', 'sample', 'synctype'])).toHaveValue(JSON.stringify('rsync'));
  await page.getByRole('tab', { name: 'Basic information', exact: true }).click();
  await expect(field(page, ['packages', 'sample', 'name'])).toHaveValue('Package sample');
});

test('synchronizes Monaco and form edits in both directions', async ({ page }) => {
  const config = configWithPackages({ sample: packageConfig('sample') });
  config.mirrorname = 'Changed in JSON';
  await replaceEditorText(page, JSON.stringify(config, null, 2));

  await expect(field(page, ['mirrorname'])).toHaveValue('Changed in JSON');
  await fillFormField(page, ['hostname'], 'cdn.example.net');

  const downloaded = JSON.parse(await downloadText(page));
  expect(downloaded.mirrorname).toBe('Changed in JSON');
  expect(downloaded.hostname).toBe('cdn.example.net');
});

test('records form changes in Monaco undo history', async ({ page }) => {
  await importConfig(page, DEFAULT_CONFIG);
  const mirrorName = field(page, ['mirrorname']);
  await fillFormField(page, ['mirrorname'], 'Temporary name');
  await expect(mirrorName).toHaveValue('Temporary name');

  await page.locator('.monaco-editor textarea').first().focus();
  await page.keyboard.press('ControlOrMeta+Z');

  await expect(mirrorName).toHaveValue(DEFAULT_CONFIG.mirrorname);
  expect(JSON.parse(await downloadText(page)).mirrorname).toBe(DEFAULT_CONFIG.mirrorname);
});

test('locks form editing and download until malformed JSON is repaired', async ({ page }) => {
  const mirrorName = field(page, ['mirrorname']);
  await replaceEditorText(page, '{ "mirrorname": ');

  await expect(page.locator('#ce-status')).toContainText(/error/i);
  await expect(mirrorName).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Download config.json' })).toBeDisabled();

  await replaceEditorText(page, JSON.stringify(DEFAULT_CONFIG, null, 2));
  await expect(page.locator('#ce-status')).toContainText(/Valid configuration|0 errors/i);
  await expect(mirrorName).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Download config.json' })).toBeEnabled();
});

test('rejects duplicate JSON keys and keeps export disabled', async ({ page }) => {
  const duplicate = JSON.stringify(DEFAULT_CONFIG, null, 2).replace(
    '{',
    '{\n  "mirrorname": "Duplicate",',
  );
  await replaceEditorText(page, duplicate);

  await expect(page.locator('#ce-status')).toContainText(/error/i);
  await expect(page.getByRole('button', { name: 'Download config.json' })).toBeDisabled();
});

test('preserves unknown and dangerous-looking keys without fetching an external schema', async ({ page }) => {
  const requests = [];
  page.on('request', (request) => requests.push(request.url()));
  const raw = JSON.stringify({
    ...DEFAULT_CONFIG,
    $schema: 'https://schema.invalid/mirror.json',
    extension: { enabled: true },
  }, null, 2).replace(
    '"extension": {',
    '"large_integer": 9007199254740993,\n  "__proto__": { "polluted": true },\n  "extension": {',
  );

  await importConfig(page, raw);
  await expect(field(page, ['mirrorname'])).toHaveValue(DEFAULT_CONFIG.mirrorname);
  await fillFormField(page, ['mirrorname'], 'Preserved Mirror');
  const downloaded = await downloadText(page);

  expect(downloaded).toContain('9007199254740993');
  expect(downloaded).toContain('"__proto__"');
  expect(JSON.parse(downloaded).extension).toEqual({ enabled: true });
  expect(await page.evaluate(() => Object.prototype.polluted)).toBeUndefined();
  expect(requests).not.toContain('https://schema.invalid/mirror.json');
  expect(requests.every((url) => new URL(url).origin === 'http://127.0.0.1:8765')).toBe(true);
});

test('offers every built-in synchronization method', async ({ page }) => {
  await importConfig(page, configWithPackages({ sample: packageConfig('sample') }));
  await selectPackage(page, 'sample');
  const synctype = field(page, ['packages', 'sample', 'synctype']);
  const options = await synctype.locator('option').evaluateAll((nodes) =>
    nodes.map((node) => JSON.parse(node.value)).filter(Boolean).sort(),
  );

  expect(options).toEqual(BUILTIN_METHODS);
  for (const method of BUILTIN_METHODS) {
    await synctype.selectOption({ label: method });
    await expect(synctype).toHaveValue(JSON.stringify(method));
  }
});

test('renders method-specific option forms for all built-in methods', async ({ page }) => {
  const methodOptions = {
    rsync: { ffts: false },
    ftpsync: { hub: false },
    lftp: { max_retries: 3 },
    bandersnatch: {},
    debmirror: { method: 'https' },
    jigdo: { jigdo_file: 'rsync://example.com/images', debian_mirror: 'https://deb.example.com' },
    local: {},
    ubuntu: { trace: true },
    'apt-mirror2': {
      config: [{ src: 'https://apt.example.com/', dst: 'stable/' }],
    },
  };
  const representativeField = {
    rsync: 'ffts',
    ftpsync: 'hub',
    lftp: 'max_retries',
    debmirror: 'method',
    jigdo: 'jigdo_file',
    ubuntu: 'trace',
    'apt-mirror2': 'config',
  };
  const packages = Object.fromEntries(BUILTIN_METHODS.map((method) => {
    const entry = packageConfig(method, method);
    entry.settings.options = methodOptions[method];
    return [method, entry];
  }));
  await importConfig(page, configWithPackages(packages));

  for (const method of BUILTIN_METHODS) {
    await selectPackage(page, method);
    await expect(field(page, ['packages', method, 'synctype'])).toHaveValue(JSON.stringify(method));
    await page.getByText(`${method} options`, { exact: true }).filter({ visible: true }).click();
    if (representativeField[method]) {
      await expect(field(page, [
        'packages', method, 'settings', 'options', representativeField[method],
      ])).toBeVisible();
    }
  }
});

test('renames a package key and prevents duplicate package IDs', async ({ page }) => {
  const config = configWithPackages({
    first: packageConfig('first'),
    second: packageConfig('second'),
  });
  await importConfig(page, config);
  await selectPackage(page, 'first');

  await page.locator('#ce-form').getByText('Package actions', { exact: true }).click();
  const firstId = field(page, ['packages', 'first', 'id']);
  await firstId.fill('renamed');
  await page.getByRole('button', { name: 'Rename package' }).click();
  await expect(field(page, ['packages', 'renamed', 'id'])).toHaveValue('renamed');
  let downloaded = JSON.parse(await downloadText(page));
  expect(downloaded.packages.first).toBeUndefined();
  expect(downloaded.packages.renamed.id).toBe('renamed');

  const renamedId = field(page, ['packages', 'renamed', 'id']);
  await renamedId.fill('second');
  await page.getByRole('button', { name: 'Rename package' }).click();
  await expect(page.locator('#ce-form .ce-error:visible')).toContainText(/unique, safe package ID/i);
  downloaded = JSON.parse(await downloadText(page));
  expect(Object.keys(downloaded.packages).sort()).toEqual(['renamed', 'second']);
});

test('formats JSON and resets to a new configuration after confirmation', async ({ page }) => {
  await replaceEditorText(page, JSON.stringify(DEFAULT_CONFIG));
  await page.getByRole('button', { name: 'Format JSON' }).click();
  expect(await downloadText(page)).toContain('\n  "mirrorname"');

  await fillFormField(page, ['mirrorname'], 'Unsaved mirror');
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'New configuration' }).click();
  await expect(field(page, ['mirrorname'])).toHaveValue('My Mirror');
  expect(JSON.parse(await downloadText(page)).packages).toEqual({});
});

test('switches between JSON and form tabs on a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 800 });
  const jsonTab = page.getByRole('tab', { name: 'JSON' });
  const formTab = page.getByRole('tab', { name: 'Settings' });

  await expect(jsonTab).toBeVisible();
  await expect(formTab).toBeVisible();
  await formTab.click();
  await expect(page.locator('#ce-form')).toBeVisible();
  await jsonTab.click();
  await expect(page.locator('.monaco-editor')).toBeVisible();
});


test('adds and deletes a package through the form', async ({ page }) => {
  await page.getByRole('button', { name: 'Add package', exact: true }).click();
  await page.getByLabel('New package ID').fill('sample');
  await page.getByLabel('New package ID').press('Tab');
  await fillFormField(page, ['packages', 'sample', 'settings', 'src'], 'rsync://example.com/repository');
  await page.getByRole('button', { name: 'Create package', exact: true }).click();
  await expect(field(page, ['packages', 'sample', 'synctype'])).toHaveValue(JSON.stringify('rsync'));
  expect(JSON.parse(await downloadText(page)).packages.sample.synctype).toBe('rsync');
  await page.locator('#ce-form').getByText('Package actions', { exact: true }).click();
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Delete package', exact: true }).click();
  expect(JSON.parse(await downloadText(page)).packages).toEqual({});
});

test('finds a package among fifty entries by ID or display name', async ({ page }) => {
  const packages = Object.fromEntries(Array.from({ length: 55 }, (_, index) => {
    const id = `repository-${index}`;
    return [id, packageConfig(id)];
  }));
  packages['repository-42'].name = 'Debian Archive';
  await importConfig(page, configWithPackages(packages));
  const search = page.getByLabel('Search packages', { exact: true });
  const selector = page.getByLabel('Configuration section');
  await search.fill('Debian');
  await expect(selector.locator('option[value="repository-42"]')).toHaveCount(1);
  await expect(selector.locator('option[value="repository-1"]')).toHaveCount(0);
  await selectPackage(page, 'repository-42');
  await expect(field(page, ['packages', 'repository-42', 'settings', 'src'])).toBeVisible();
  await search.fill('repository-12');
  await selectPackage(page, 'repository-12');
  await expect(field(page, ['packages', 'repository-12', 'settings', 'dst'])).toHaveValue('/srv/mirror/repository-12');
});

test('retains the selected package tab while committing an input', async ({ page }) => {
  await importConfig(page, configWithPackages({ sample: packageConfig('sample') }));
  await selectPackage(page, 'sample');
  const basic = page.getByRole('tab', { name: 'Basic information', exact: true });
  await basic.click();
  await fillFormField(page, ['packages', 'sample', 'name'], 'A friendlier name');
  await expect(basic).toHaveAttribute('aria-selected', 'true');
  await expect(field(page, ['packages', 'sample', 'name'])).toHaveValue('A friendlier name');
  expect(JSON.parse(await downloadText(page)).packages.sample.name).toBe('A friendlier name');
});

test('opening package tabs preserves omitted options and raw extension values', async ({ page }) => {
  const config = configWithPackages({ sample: packageConfig('sample') });
  delete config.packages.sample.disabled;
  config.packages.sample.settings.options = { custom_extension: { enabled: false, text: '' } };
  await importConfig(page, config);
  await selectPackage(page, 'sample');
  for (const name of ['Basic information', 'Links', 'Advanced', 'Sync']) {
    await page.getByRole('tab', { name, exact: true }).click();
  }
  expect(JSON.parse(await downloadText(page))).toEqual(config);
});

test('edits package links independently from synchronization settings', async ({ page }) => {
  await importConfig(page, configWithPackages({ sample: packageConfig('sample') }));
  await selectPackage(page, 'sample');
  await expect(field(page, ['packages', 'sample', 'link', 0, 'href'])).not.toBeVisible();
  await page.getByRole('tab', { name: 'Links', exact: true }).click();
  await fillFormField(page, ['packages', 'sample', 'link', 0, 'href'], 'https://new.example.org/');
  const result = JSON.parse(await downloadText(page));
  expect(result.packages.sample.link).toEqual([{ rel: 'HOME', href: 'https://new.example.org/' }]);
  expect(result.packages.sample.settings.src).toBe('rsync://example.com/repository');
});

test('focuses the selected package in JSON and provides a settings-only view', async ({ page }) => {
  const packages = Object.fromEntries(Array.from({ length: 50 }, (_, index) => {
    const id = `repository-${index}`;
    return [id, packageConfig(id)];
  }));
  await importConfig(page, configWithPackages(packages));
  await selectPackage(page, 'repository-42');
  await expect(page.locator('.ce-json-highlight').first()).toBeVisible();
  await expect(page.locator('.monaco-editor .view-lines')).toContainText('repository-42');
  await page.getByRole('button', { name: 'Settings only', exact: true }).click();
  await expect(page.locator('.monaco-editor')).not.toBeVisible();
  await expect(field(page, ['packages', 'repository-42', 'settings', 'src'])).toBeVisible();
  await page.getByRole('button', { name: 'Split view', exact: true }).click();
  await expect(page.locator('.monaco-editor')).toBeVisible();
});

test('toggles documentation navigation without losing form edits', async ({ page }) => {
  await fillFormField(page, ['mirrorname'], 'More room for settings');
  await page.getByRole('button', { name: 'Hide documentation navigation', exact: true }).click();
  await expect(page.locator('.wy-nav-side')).not.toBeVisible();
  await page.getByRole('button', { name: 'Show documentation navigation', exact: true }).click();
  await expect(page.locator('.wy-nav-side')).toBeVisible();
  expect(JSON.parse(await downloadText(page)).mirrorname).toBe('More room for settings');
});

test('keeps the active form controls attached when committing a value', async ({ page }) => {
  await importConfig(page, configWithPackages({ sample: packageConfig('sample') }));
  await selectPackage(page, 'sample');
  const source = field(page, ['packages', 'sample', 'settings', 'src']);
  const originalControl = await source.elementHandle();
  await source.fill('rsync://new.example.org/archive');
  await source.press('Tab');
  await expect.poll(() => originalControl.evaluate((node) => node.isConnected)).toBe(true);
  expect(await page.evaluate(() => document.activeElement?.closest('#ce-form') !== null)).toBe(true);
  expect(JSON.parse(await downloadText(page)).packages.sample.settings.src).toBe('rsync://new.example.org/archive');
});

test('edits a friendly interval and retains complex duration text', async ({ page }) => {
  const config = configWithPackages({ sample: packageConfig('sample'), complex: packageConfig('complex') });
  config.packages.complex.syncrate = 'P1DT2H30M';
  await importConfig(page, config);
  await selectPackage(page, 'sample');
  await expect(field(page, ['packages', 'sample', 'syncrate'])).toHaveValue('6');
  await expect(page.getByLabel('Sync interval unit', { exact: true }).filter({ visible: true })).toHaveValue('H');
  await fillFormField(page, ['packages', 'sample', 'syncrate'], '30');
  await page.getByLabel('Sync interval unit', { exact: true }).filter({ visible: true }).selectOption('M');
  expect(JSON.parse(await downloadText(page)).packages.sample.syncrate).toBe('PT30M');
  await selectPackage(page, 'complex');
  await expect(field(page, ['packages', 'complex', 'syncrate'])).toHaveValue('P1DT2H30M');
  expect(JSON.parse(await downloadText(page)).packages.complex.syncrate).toBe('P1DT2H30M');
});

test('keeps invalid input visible until explicitly discarded', async ({ page }) => {
  await importConfig(page, configWithPackages({ sample: packageConfig('sample') }));
  await selectPackage(page, 'sample');
  const interval = field(page, ['packages', 'sample', 'syncrate']);
  await interval.fill('-1');
  await interval.press('Tab');
  await expect(page.getByRole('button', { name: 'Download config.json' })).toBeDisabled();
  await page.getByRole('tab', { name: 'Links', exact: true }).click();
  await expect(interval).toBeVisible();
  await expect(interval).toHaveValue('-1');
  await page.getByRole('button', { name: 'Discard input', exact: true }).click();
  await expect(interval).toHaveValue('6');
  expect(JSON.parse(await downloadText(page)).packages.sample.syncrate).toBe('PT6H');
});

test('preserves an expanded options section when a field changes', async ({ page }) => {
  const config = configWithPackages({ sample: packageConfig('sample') });
  config.packages.sample.settings.options = { ffts: false };
  await importConfig(page, config);
  await selectPackage(page, 'sample');
  await page.getByText('rsync options', { exact: true }).filter({ visible: true }).click();
  const option = field(page, ['packages', 'sample', 'settings', 'options', 'ffts']);
  await option.check();
  await expect(option).toBeVisible();
  await expect(option).toBeChecked();
  expect(JSON.parse(await downloadText(page)).packages.sample.settings.options.ffts).toBe(true);
});

test('opens the package and tab for a validation error', async ({ page }) => {
  const config = configWithPackages({ sample: packageConfig('sample') });
  config.packages.sample.syncrate = 'not-an-interval';
  await importConfig(page, config);
  await page.locator('#ce-issues-heading').click();
  await page.locator('#ce-issues button').filter({ hasText: 'syncrate' }).first().click();
  await expect(page.getByLabel('Configuration section')).toHaveValue('sample');
  await expect(page.getByRole('tab', { name: 'Sync', exact: true })).toHaveAttribute('aria-selected', 'true');
  const interval = field(page, ['packages', 'sample', 'syncrate']);
  await expect(interval).toHaveValue('not-an-interval');
  await expect(interval).toHaveAttribute('aria-invalid', 'true');
  await fillFormField(page, ['packages', 'sample', 'syncrate'], 'PT6H');
  await expect(page.getByRole('button', { name: 'Download config.json' })).toBeEnabled();
});

for (const method of BUILTIN_METHODS) {
  test(`creates a ${method} package with its relevant settings`, async ({ page }) => {
    await page.getByRole('button', { name: 'Add package', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Add package', exact: true });
    await dialog.getByLabel('New package ID').fill('created');
    await dialog.getByLabel('New package ID').press('Tab');
    await dialog.getByLabel('New package sync type').selectOption(method);
    if (method !== 'local') {
      await fillFormField(page, ['packages', 'created', 'settings', 'src'], 'rsync://example.com/repository');
    }
    if (method === 'jigdo') {
      if (!await dialog.locator('.ce-method-options').evaluate((node) => node.open)) {
        await dialog.getByText('jigdo options', { exact: true }).click();
      }
      await fillFormField(page, ['packages', 'created', 'settings', 'options', 'jigdo_file'], 'rsync://example.com/images');
      await fillFormField(page, ['packages', 'created', 'settings', 'options', 'debian_mirror'], 'https://deb.example.com/');
    }
    if (method === 'apt-mirror2') {
      if (!await dialog.locator('.ce-method-options').evaluate((node) => node.open)) {
        await dialog.getByText('apt-mirror2 options', { exact: true }).click();
      }
      await dialog.getByRole('button', { name: 'Add Config item', exact: true }).click();
      await dialog.getByText('Repository 1', { exact: true }).click();
      await fillFormField(page, ['packages', 'created', 'settings', 'options', 'config', 0, 'src'], 'https://apt.example.com/');
      await fillFormField(page, ['packages', 'created', 'settings', 'options', 'config', 0, 'dst'], 'stable/');
      await field(page, ['packages', 'created', 'settings', 'options', 'config', 0, 'keyring']).getByRole('button').click();
      await fillFormField(page, ['packages', 'created', 'settings', 'options', 'config', 0, 'keyring'], '/usr/share/keyrings/debian-archive-keyring.gpg');
    }
    await dialog.getByRole('button', { name: 'Create package', exact: true }).click();
    await expect(dialog).not.toBeVisible();
    const result = JSON.parse(await downloadText(page));
    expect(result.packages.created.id).toBe('created');
    expect(result.packages.created.synctype).toBe(method);
    expect(result.packages.created.settings.dst).toBe('/srv/mirror/created');
  });
}

test('navigates package tabs with the keyboard', async ({ page }) => {
  await importConfig(page, configWithPackages({ sample: packageConfig('sample') }));
  await selectPackage(page, 'sample');
  const sync = page.getByRole('tab', { name: 'Sync', exact: true });
  await sync.focus();
  await page.keyboard.press('ArrowRight');
  const basic = page.getByRole('tab', { name: 'Basic information', exact: true });
  await expect(basic).toBeFocused();
  await expect(basic).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('End');
  await expect(page.getByRole('tab', { name: 'Advanced', exact: true })).toBeFocused();
  await page.keyboard.press('Home');
  await expect(sync).toBeFocused();
});

test('preserves text during composition until the input is committed', async ({ page }) => {
  await importConfig(page, configWithPackages({ sample: packageConfig('sample') }));
  await selectPackage(page, 'sample');
  await page.getByRole('tab', { name: 'Basic information', exact: true }).click();
  const name = field(page, ['packages', 'sample', 'name']);
  await name.focus();
  await name.dispatchEvent('compositionstart', { data: '' });
  await name.evaluate((node) => {
    node.value = '한국 미러';
    node.dispatchEvent(new InputEvent('input', { bubbles: true, data: '한국 미러', inputType: 'insertCompositionText', isComposing: true }));
  });
  await page.getByRole('tab', { name: 'Links', exact: true }).click();
  await expect(name).toBeVisible();
  await expect(name).toHaveValue('한국 미러');
  await name.dispatchEvent('compositionend', { data: '한국 미러' });
  await name.press('Tab');
  expect(JSON.parse(await downloadText(page)).packages.sample.name).toBe('한국 미러');
});

test('reveals unknown plugin settings in JSON without changing them', async ({ page }) => {
  const config = configWithPackages({ sample: packageConfig('sample', 'custom-plugin') });
  config.packages.sample.plugin_data = { endpoint: 'https://plugin.example.org/', flags: [false, ''] };
  await importConfig(page, config);
  await selectPackage(page, 'sample');
  await page.getByRole('button', { name: 'Settings only', exact: true }).click();
  await page.getByRole('tab', { name: 'Advanced', exact: true }).click();
  await field(page, ['packages', 'sample', 'plugin_data']).getByRole('button', { name: 'Show in JSON', exact: true }).click();
  await expect(page.locator('.monaco-editor')).toBeVisible();
  await expect(page.locator('.ce-json-highlight').first()).toBeVisible();
  expect(JSON.parse(await downloadText(page))).toEqual(config);
});

for (const viewport of [{ width: 1280, height: 800 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
  test(`shows essential settings without pane scrolling at ${viewport.width}×${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const packages = Object.fromEntries(Array.from({ length: 55 }, (_, index) => {
      const id = `repository-${index}`;
      return [id, packageConfig(id)];
    }));
    await importConfig(page, configWithPackages(packages));
    await selectPackage(page, 'repository-42');
    const form = page.locator('#ce-form');
    await expect.poll(() => form.evaluate((node) => node.scrollTop)).toBe(0);
    const bounds = await form.boundingBox();
    for (const path of [
      ['packages', 'repository-42', 'settings', 'src'],
      ['packages', 'repository-42', 'settings', 'dst'],
      ['packages', 'repository-42', 'syncrate'],
    ]) {
      const box = await field(page, path).boundingBox();
      expect(box.y).toBeGreaterThanOrEqual(bounds.y);
      expect(box.y + box.height).toBeLessThanOrEqual(bounds.y + bounds.height);
      expect(box.x).toBeGreaterThanOrEqual(bounds.x);
      expect(box.x + box.width).toBeLessThanOrEqual(bounds.x + bounds.width);
    }
  });
}

test('closes package creation with Escape and returns focus to Add package', async ({ page }) => {
  const add = page.getByRole('button', { name: 'Add package', exact: true });
  await add.click();
  const dialog = page.getByRole('dialog', { name: 'Add package', exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel('New package ID').fill('unfinished');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(add).toBeFocused();
  expect(JSON.parse(await downloadText(page)).packages).toEqual({});
});

test('requires an explicit choice when JSON changes an unfinished form value', async ({ page }) => {
  const config = configWithPackages({ sample: packageConfig('sample') });
  await importConfig(page, config);
  await selectPackage(page, 'sample');
  const interval = field(page, ['packages', 'sample', 'syncrate']);
  await interval.fill('-1');
  await interval.press('Tab');
  config.packages.sample.syncrate = 'PT12H';
  await replaceEditorText(page, JSON.stringify(config, null, 2));
  await expect(page.locator('#ce-draft-message')).toContainText('This value also changed in JSON');
  await expect(interval).toHaveValue('-1');
  await expect(page.getByRole('button', { name: 'Download config.json' })).toBeDisabled();
  await page.getByRole('button', { name: 'Discard input', exact: true }).click();
  await expect(interval).toHaveValue('12');
  expect(JSON.parse(await downloadText(page)).packages.sample.syncrate).toBe('PT12H');
});

test('repairing a draft to its original value clears errors without an extra undo step', async ({ page }) => {
  await importConfig(page, configWithPackages({ sample: packageConfig('sample') }));
  await selectPackage(page, 'sample');
  await fillFormField(page, ['packages', 'sample', 'settings', 'src'], 'rsync://new.example.org/archive');
  const interval = field(page, ['packages', 'sample', 'syncrate']);
  await interval.fill('-1');
  await interval.press('Tab');
  await expect(page.getByRole('button', { name: 'Download config.json' })).toBeDisabled();
  await interval.fill('6');
  await interval.press('Tab');
  await expect(page.getByRole('button', { name: 'Download config.json' })).toBeEnabled();
  await expect(page.locator('#ce-draft-notice')).not.toBeVisible();
  await page.locator('.monaco-editor textarea').first().focus();
  await page.keyboard.press('ControlOrMeta+Z');
  await expect(field(page, ['packages', 'sample', 'settings', 'src'])).toHaveValue('rsync://example.com/repository');
  expect(JSON.parse(await downloadText(page)).packages.sample.syncrate).toBe('PT6H');
});


test('copies committed JSON while retaining an invalid form draft', async ({ page }) => {
  const config = configWithPackages({ sample: packageConfig('sample') });
  await importConfig(page, config);
  await selectPackage(page, 'sample');
  const interval = field(page, ['packages', 'sample', 'syncrate']);
  await interval.fill('-1');
  await interval.press('Tab');
  await page.getByRole('button', { name: 'Copy JSON', exact: true }).click();
  await expect(page.locator('#ce-notice')).toHaveText('JSON copied.');
  expect(JSON.parse(await page.evaluate(() => navigator.clipboard.readText()))).toEqual(config);
  await expect(interval).toHaveValue('-1');
  await expect(page.getByRole('button', { name: 'Download config.json' })).toBeDisabled();
});
