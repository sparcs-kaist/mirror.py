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
  return page.locator(`[data-path='${JSON.stringify(path)}']`);
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
  await expect(field(page, ['packages', packageId, 'id'])).toBeVisible();
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
  await expect(field(page, ['packages', 'sample', 'name'])).toHaveValue('Package sample');
  await expect(field(page, ['packages', 'sample', 'synctype'])).toHaveValue(JSON.stringify('rsync'));
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
    await expect(field(page, ['packages', method, 'settings', 'options'])).toBeVisible();
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
  await page.getByLabel('New package ID').fill('sample');
  await page.getByRole('button', { name: 'Add package', exact: true }).click();
  await expect(field(page, ['packages', 'sample', 'id'])).toHaveValue('sample');
  expect(JSON.parse(await downloadText(page)).packages.sample.synctype).toBe('rsync');
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Delete package', exact: true }).click();
  expect(JSON.parse(await downloadText(page)).packages).toEqual({});
});
