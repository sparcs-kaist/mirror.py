const BUILTIN_METHODS = [
  "rsync",
  "ftpsync",
  "lftp",
  "bandersnatch",
  "ubuntu",
  "jigdo",
  "local",
  "debmirror",
  "apt-mirror2",
];

const RESERVED_PACKAGE_IDS = new Set([
  "get",
  "items",
  "keys",
  "values",
  "to_dict",
  "_keys",
  "_reserved_attrs",
  "_validate_id",
]);

const stringList = (description) => ({
  type: "array",
  items: { type: "string" },
  description,
});

const stringOrList = (description) => ({
  anyOf: [{ type: "string" }, stringList(description)],
  description,
});

const optionSchema = (title, properties, required = [], additionalProperties = true) => ({
  title,
  type: "object",
  properties,
  required,
  additionalProperties,
  "x-group": "Method options",
});

export const methodSchemas = {
  rsync: optionSchema("rsync options", {
    ffts: { type: "boolean", default: false, description: "Run the FFTS timestamp pre-check." },
    fftsfile: { type: "string", default: "", description: "Remote FFTS timestamp filename." },
    user: { type: "string", default: "", description: "rsync username." },
    password: { type: "string", default: "", description: "rsync password.", "x-secret": true },
    option_include: {
      type: "string",
      default: "",
      pattern: "^[vrlptDHaznhPxWENcimub]*$",
      description: "Flag characters added to the default rsync flags.",
    },
    option_exclude: {
      type: "string",
      default: "",
      pattern: "^[^-\\s\\u0000-\\u001f\\u007f]*$",
      description: "Flag characters removed from the default rsync flags.",
    },
    exclude: stringList("Additional rsync exclude patterns."),
  }),

  ftpsync: optionSchema("ftpsync options", {
    path: { type: "string", description: "Remote rsync module path; required for a bare-host source." },
    hub: { anyOf: [{ type: "boolean" }, { type: "string" }], default: false },
    user: { type: "string", description: "rsync username." },
    password: { type: "string", description: "rsync password.", "x-secret": true },
    email: { type: "string", description: "Notification address." },
    maintainer: { type: "string" },
    sponsor: { type: "string" },
    country: { type: "string" },
    location: { type: "string" },
    throughput: { type: "string" },
    arch_include: { type: "string", description: "Space-separated architectures to include." },
    arch_exclude: { type: "string", description: "Space-separated architectures to exclude." },
    logdir: { type: "string", description: "Override the ftpsync log directory." },
  }),

  lftp: optionSchema("lftp options", {
    list_options: { type: "string", enum: ["-a"], description: "Include hidden files in listings." },
    scan_all_first: { type: "boolean", default: false },
    exclude_x: stringList("Regular expressions passed as lftp -x exclusions."),
    exclude_X: stringList("Regular expressions passed as lftp -X exclusions."),
    exclude: stringList("Glob patterns passed as lftp --exclude options."),
    max_retries: { type: "integer", minimum: 1, maximum: 100, default: 3 },
    net_timeout: { type: "integer", minimum: 1, maximum: 3600, default: 60 },
  }),

  bandersnatch: optionSchema("bandersnatch options", {}),

  ubuntu: optionSchema("ubuntu options", {
    trace: { type: "boolean", default: true },
    extra_rsync_args: stringList("Additional arguments appended to both rsync stages."),
    stage1_excludes: stringList("Patterns excluded during the first rsync stage."),
    user: { type: "string", default: "" },
    password: { type: "string", default: "", "x-secret": true },
  }),

  jigdo: optionSchema(
    "jigdo options",
    {
      jigdo_file: { type: "string", description: "rsync URL or path to the jigdo-file index." },
      debian_mirror: { type: "string", description: "Debian mirror used to assemble images." },
      timeout: { type: "integer", default: 7200 },
      trace: { type: "boolean", default: true },
      trace_path: { type: "string", default: "project/trace" },
      template_excludes: stringList("Phase-one exclude patterns."),
      final_includes: stringList("ISO patterns included by the final rsync stage."),
      extra_rsync_args: stringList("Additional arguments for both rsync stages."),
      rsync_bin: { type: "string", default: "rsync" },
      jigdo_mirror_bin: { type: "string", default: "jigdo-mirror" },
      hostname: { type: "string" },
      user: { type: "string", default: "" },
      password: { type: "string", default: "", "x-secret": true },
    },
    ["jigdo_file", "debian_mirror"],
  ),

  local: optionSchema("local options", {}),

  debmirror: optionSchema("debmirror options", {
    method: { type: "string", enum: ["ftp", "http", "https", "rsync", "file"] },
    host: { type: "string" },
    root: { type: "string" },
    dist: stringOrList("Distributions to mirror."),
    section: stringOrList("Archive components to mirror."),
    arch: stringOrList("Binary architectures to mirror."),
    source: { type: "boolean", default: false },
    check_gpg: { type: "boolean", default: true },
    keyring: stringOrList("Absolute paths to trusted keyrings."),
    ignore_release_gpg: { type: "boolean", default: false },
    ignore_missing_release: { type: "boolean", default: false },
    cleanup: { type: "string", enum: ["postcleanup", "precleanup", "nocleanup"], default: "postcleanup" },
    diff: { type: "string", enum: ["use", "mirror", "none"] },
    rsync_extra: {
      anyOf: [
        { type: "string", enum: ["doc", "indices", "tools", "trace", "none"] },
        { type: "array", items: { type: "string", enum: ["doc", "indices", "tools", "trace", "none"] } },
      ],
    },
    i18n: { type: "boolean", default: false },
    getcontents: { type: "boolean", default: false },
    di_dist: stringOrList("Debian Installer distributions."),
    di_arch: stringOrList("Debian Installer architectures."),
    proxy: { type: "string" },
    passive: { type: "boolean", default: false },
    user: { type: "string" },
    password: { type: "string", "x-secret": true },
    exclude: stringOrList("Package exclusion expressions."),
    include: stringOrList("Package inclusion expressions."),
    exclude_deb_section: stringOrList("Debian sections to exclude."),
    limit_priority: stringOrList("Package priorities to limit."),
    rsync_options: { type: "string" },
    timeout: { type: "integer", minimum: 1 },
    allow_dist_rename: { type: "boolean", default: false },
    omit_suite_symlinks: { type: "boolean", default: false },
  }),

  "apt-mirror2": optionSchema(
    "apt-mirror2 options",
    {
      config: {
        type: "array",
        minItems: 1,
        description: "Repositories mirrored below the package destination.",
        items: {
          type: "object",
          required: ["src", "dst"],
          additionalProperties: false,
          properties: {
            src: { type: "string", description: "HTTP, HTTPS, or FTP repository root." },
            dst: { type: "string", description: "Safe relative destination directory." },
            dist: stringOrList("Distribution names or flat paths ending in '/'."),
            section: stringOrList("Repository components."),
            arch: stringOrList("Binary architectures."),
            source: { type: "boolean" },
            check_gpg: { type: "boolean", default: true },
            keyring: stringOrList("Absolute trusted keyring paths."),
          },
        },
      },
      source: { type: "boolean", default: false },
      nthreads: { type: "integer", minimum: 1, default: 8 },
      limit_rate: {
        anyOf: [
          { type: "integer", minimum: 1 },
          { type: "string", pattern: "^[1-9][0-9]*[kKmM]?$" },
        ],
      },
    },
    ["config"],
    false,
  ),
};

const linkSchema = {
  type: "object",
  required: ["rel", "href"],
  additionalProperties: true,
  properties: {
    rel: { type: "string", "x-label": "Relationship" },
    href: { type: "string", "x-label": "URL" },
  },
};

const packageSettingsSchema = {
  type: "object",
  required: ["hidden", "src", "dst"],
  additionalProperties: true,
  properties: {
    hidden: { type: "boolean", default: false, "x-group": "Package" },
    src: { type: "string", "x-label": "Source", "x-group": "Package" },
    dst: { type: "string", "x-label": "Destination", "x-group": "Package" },
    options: { type: "object", default: {}, additionalProperties: true, "x-group": "Method options" },
  },
};

const methodConditions = BUILTIN_METHODS.map((method) => ({
  if: {
    type: "object",
    required: ["synctype"],
    properties: { synctype: { const: method } },
  },
  then: {
    properties: {
      settings: {
        properties: {
          options: { $ref: `#/definitions/methodOptions/${method}` },
        },
      },
    },
  },
}));

export const packageSchema = {
  title: "Package",
  type: "object",
  required: ["name", "id", "href", "synctype", "syncrate", "link", "settings"],
  additionalProperties: true,
  properties: {
    name: { type: "string", "x-label": "Display name", "x-group": "Package" },
    id: { type: "string", "x-label": "Package ID", "x-group": "Package" },
    href: { type: "string", "x-label": "Web path", "x-group": "Package" },
    synctype: {
      type: "string",
      "x-label": "Sync method",
      "x-group": "Scheduling",
      examples: BUILTIN_METHODS,
    },
    syncrate: {
      type: "string",
      "x-label": "Sync interval",
      "x-group": "Scheduling",
      description: "ISO 8601 duration, PUSH, or an empty string.",
    },
    disabled: { type: "boolean", default: false, "x-group": "Scheduling" },
    link: { type: "array", items: linkSchema, "x-label": "Reference links", "x-group": "Package" },
    settings: packageSettingsSchema,
  },
  allOf: methodConditions,
};

const fileFormatSchema = {
  type: "object",
  required: ["base", "folder", "filename"],
  additionalProperties: true,
  properties: {
    base: { type: "string" },
    folder: { type: "string" },
    filename: { type: "string" },
    gzip: { type: "boolean", default: true },
  },
};

const settingsSchema = {
  title: "Global settings",
  type: "object",
  required: ["logfolder", "webroot", "statusfile", "statfile", "localtimezone", "logger"],
  additionalProperties: true,
  properties: {
    logfolder: { type: "string", "x-group": "Paths" },
    webroot: { type: "string", "x-group": "Paths" },
    statusfile: { type: "string", "x-group": "Paths" },
    statfile: { type: "string", "x-group": "Paths" },
    socket_path: { type: "string", "x-group": "Paths" },
    uid: { type: "integer", default: 0, "x-group": "Process" },
    gid: { type: "integer", default: 0, "x-group": "Process" },
    localtimezone: { type: "string", "x-label": "Timezone", "x-group": "Process" },
    errorcontinuetime: { type: "integer", default: 60, "x-group": "Scheduling" },
    max_runtime: {
      type: "string",
      default: "",
      description: "ISO 8601 duration, PUSH, or empty to disable the runtime limit.",
      "x-group": "Scheduling",
    },
    maintainer: {
      type: "object",
      additionalProperties: true,
      "x-group": "Identity",
      properties: {
        name: { type: "string" },
        email: { type: "string" },
      },
    },
    logger: {
      type: "object",
      required: ["level", "format", "fileformat"],
      additionalProperties: true,
      "x-group": "Logging",
      properties: {
        level: { type: "string" },
        packagelevel: { type: "string", default: "ERROR" },
        format: { type: "string" },
        packageformat: { type: "string" },
        fileformat: fileFormatSchema,
        packagefileformat: fileFormatSchema,
      },
    },
    ftpsync: {
      type: "object",
      additionalProperties: false,
      "x-group": "ftpsync defaults",
      properties: {
        maintainer: { type: "string", default: "" },
        sponsor: { type: "string", default: "" },
        country: { type: "string", default: "" },
        location: { type: "string", default: "" },
        throughput: { type: "string", default: "" },
        include: { type: "string", default: "" },
        exclude: { type: "string", default: "" },
      },
    },
    plugins: {
      default: {},
      "x-group": "Plugins",
      anyOf: [
        {
          type: "object",
          additionalProperties: {
            type: "object",
            additionalProperties: true,
            properties: { enabled: { type: "boolean", default: true } },
          },
        },
        {
          type: "array",
          items: { type: "string" },
          description: "Deprecated list-of-paths plugin configuration.",
        },
      ],
    },
    socket: {
      type: "object",
      additionalProperties: true,
      "x-group": "Socket",
      properties: {
        uid: { type: "integer" },
        gid: { type: "integer" },
        mode: { type: "string", pattern: "^(?:0o)?[0-7]{1,4}$", default: "0600" },
      },
    },
  },
};

export const configSchema = {
  $schema: "http://json-schema.org/draft-07/schema#",
  $id: "https://mirror-py.example/schema/config.json",
  title: "mirror.py configuration",
  type: "object",
  required: ["settings"],
  additionalProperties: true,
  properties: {
    mirrorname: { type: "string", "x-label": "Mirror name", "x-group": "Identity" },
    hostname: { type: "string", "x-label": "Hostname", "x-group": "Identity" },
    lastsettingmodified: { type: "integer", "x-group": "Advanced" },
    settings: settingsSchema,
    packages: {
      type: "object",
      additionalProperties: { $ref: "#/definitions/package" },
    },
  },
  definitions: {
    package: packageSchema,
    methodOptions: methodSchemas,
  },
};

export const globalSchema = configSchema;

export function createDefaultConfig() {
  return {
    mirrorname: "My Mirror",
    hostname: "mirror.example.org",
    settings: {
      logfolder: "/var/log/mirror/ftpsync",
      webroot: "/var/www/mirror",
      statusfile: "/var/www/mirror/status.json",
      statfile: "/var/lib/mirror/stat.json",
      uid: 0,
      gid: 0,
      localtimezone: "UTC",
      errorcontinuetime: 60,
      maintainer: { name: "Mirror Admin", email: "admin@example.org" },
      logger: {
        level: "INFO",
        packagelevel: "INFO",
        format: "[%(asctime)s] %(levelname)s # %(message)s",
        packageformat: "[%(asctime)s][{package}] %(levelname)s # %(message)s",
        fileformat: {
          base: "/var/log/mirror",
          folder: "{year}/{month}",
          filename: "{year}-{month}-{day}.log",
          gzip: true,
        },
        packagefileformat: {
          base: "/var/log/mirror/packages",
          folder: "{year}/{month}/{day}",
          filename: "{packageid}.{hour}:{minute}:{second}.{microsecond}.log",
          gzip: true,
        },
      },
      ftpsync: {
        maintainer: "Mirror Admins <admin@example.org>",
        sponsor: "Example <https://example.org>",
        country: "",
        location: "",
        throughput: "",
      },
    },
    packages: {},
  };
}

export function createPackage(id, method = "rsync") {
  const options = {};
  if (method === "jigdo") {
    options.jigdo_file = "";
    options.debian_mirror = "";
  } else if (method === "apt-mirror2") {
    options.config = [];
  }
  return {
    name: id || "New Package",
    id,
    href: id ? `/${id}` : "",
    synctype: method,
    syncrate: method === "local" ? "" : method === "ftpsync" ? "PUSH" : "PT6H",
    link: [],
    settings: {
      hidden: false,
      src: "",
      dst: id ? `/srv/mirror/${id}` : "",
      options,
    },
  };
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function typeMatches(value, type) {
  if (Array.isArray(type)) return type.some((item) => typeMatches(value, item));
  if (type === "object") return isObject(value);
  if (type === "array") return Array.isArray(value);
  if (type === "integer") return typeof value === "number" && Number.isInteger(value);
  if (type === "number") return typeof value === "number" && Number.isFinite(value);
  if (type === "null") return value === null;
  return typeof value === type;
}

function resolveRef(ref) {
  if (!ref.startsWith("#/")) return null;
  return ref.slice(2).split("/").reduce((node, part) => node?.[part.replaceAll("~1", "/").replaceAll("~0", "~")], configSchema);
}

function checkSchema(value, schema, path, issues) {
  if (!schema || typeof schema !== "object") return;
  if (schema.$ref) {
    checkSchema(value, resolveRef(schema.$ref), path, issues);
    return;
  }
  if (schema.allOf) {
    for (const part of schema.allOf) checkSchema(value, part, path, issues);
  }
  if (schema.if) {
    const probe = [];
    checkSchema(value, schema.if, path, probe);
    checkSchema(value, probe.length === 0 ? schema.then : schema.else, path, issues);
  }
  if (schema.anyOf) {
    const matches = schema.anyOf.some((part) => {
      const probe = [];
      checkSchema(value, part, path, probe);
      return probe.length === 0;
    });
    if (!matches) issues.push({ path, message: "Value does not match any allowed type.", severity: "error" });
    return;
  }
  if (Object.hasOwn(schema, "const") && value !== schema.const) {
    issues.push({ path, message: `Must be ${JSON.stringify(schema.const)}.`, severity: "error" });
    return;
  }
  if (schema.type && !typeMatches(value, schema.type)) {
    const expected = Array.isArray(schema.type) ? schema.type.join(" or ") : schema.type;
    issues.push({ path, message: `Expected ${expected}.`, severity: "error" });
    return;
  }
  if (schema.enum && !schema.enum.includes(value)) {
    issues.push({ path, message: `Must be one of: ${schema.enum.join(", ")}.`, severity: "error" });
  }
  if (typeof value === "string" && schema.pattern && !(new RegExp(schema.pattern)).test(value)) {
    issues.push({ path, message: "Value has an invalid format.", severity: "error" });
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) {
      issues.push({ path, message: `Must be at least ${schema.minimum}.`, severity: "error" });
    }
    if (schema.maximum !== undefined && value > schema.maximum) {
      issues.push({ path, message: `Must be at most ${schema.maximum}.`, severity: "error" });
    }
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) {
      issues.push({ path, message: `Must contain at least ${schema.minItems} item(s).`, severity: "error" });
    }
    value.forEach((item, index) => checkSchema(item, schema.items, [...path, index], issues));
  }
  if (isObject(value)) {
    for (const key of schema.required || []) {
      if (!Object.hasOwn(value, key)) {
        issues.push({ path: [...path, key], message: "Required value is missing.", severity: "error" });
      }
    }
    for (const [key, child] of Object.entries(value)) {
      if (Object.hasOwn(schema.properties || {}, key)) {
        checkSchema(child, schema.properties[key], [...path, key], issues);
      } else if (schema.additionalProperties === false) {
        issues.push({ path: [...path, key], message: "Unknown property.", severity: "error" });
      } else if (isObject(schema.additionalProperties)) {
        checkSchema(child, schema.additionalProperties, [...path, key], issues);
      }
    }
  }
}

function validDuration(value) {
  if (value === "" || value === "PUSH") return true;
  if (typeof value !== "string") return false;
  const match = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/.exec(value);
  if (!match) return false;
  if (!match.slice(1).some(Boolean)) return false;
  if (value.includes("T") && !match.slice(2).some(Boolean)) return false;
  return true;
}

function asList(value) {
  if (typeof value === "string") return value.split(",");
  return Array.isArray(value) ? value : [];
}

function canonicalUrl(value) {
  try {
    const url = new URL(value);
    url.hash = "";
    return url.toString().replace(/\/$/, "");
  } catch {
    return null;
  }
}

function validAptUrl(value) {
  if (typeof value !== "string" || !value || /[\s$#\u0000-\u001f\u007f]/.test(value)) return false;
  try {
    const url = new URL(value);
    if (!["http:", "https:", "ftp:"].includes(url.protocol)) return false;
    if (!url.hostname || url.username || url.password || url.search || url.hash) return false;
    const decodedPath = decodeURIComponent(url.pathname);
    return !decodedPath.split("/").includes("..");
  } catch {
    return false;
  }
}

function validAptSelection(value, pattern, allowFlat = false) {
  const values = asList(value);
  if (values.length === 0) return false;
  return values.every((item) => {
    if (typeof item !== "string" || !item || /[\u0000-\u001f\u007f]/.test(item)) return false;
    if (allowFlat && item.endsWith("/")) {
      if (item === "./") return true;
      const parts = item.slice(0, -1).split("/");
      return parts.every((part) => part && part !== "." && part !== ".." && /^[A-Za-z0-9][A-Za-z0-9._+/-]*$/.test(part));
    }
    return pattern.test(item);
  });
}

function validateAptMirror(packageValue, packagePath, issues) {
  const settings = packageValue.settings;
  if (!isObject(settings)) return;
  const dst = settings.dst;
  if (typeof dst === "string" && (!dst.startsWith("/") || dst === "/" || /[\s$#]/.test(dst))) {
    issues.push({ path: [...packagePath, "settings", "dst"], message: "apt-mirror2 destination must be a non-root absolute path without whitespace, '$', or '#'.", severity: "error" });
  }
  const config = settings.options?.config;
  if (!Array.isArray(config)) return;
  const sources = new Map();
  const destinations = [];
  config.forEach((repo, index) => {
    if (!isObject(repo)) return;
    const base = [...packagePath, "settings", "options", "config", index];
    const src = canonicalUrl(repo.src);
    if (!src || !validAptUrl(repo.src)) {
      issues.push({ path: [...base, "src"], message: "Repository source must be an HTTP, HTTPS, or FTP URL.", severity: "error" });
    } else if (sources.has(src)) {
      issues.push({ path: [...base, "src"], message: "Repository source URLs must be unique.", severity: "error" });
    } else {
      sources.set(src, index);
    }
    if (typeof repo.dst === "string") {
      const trimmed = repo.dst.replace(/\/+$/, "");
      if (!trimmed || trimmed.startsWith("/") || trimmed === "." || repo.dst.includes("\\") || /[\s$#\u0000-\u001f\u007f]/.test(repo.dst) || trimmed.split("/").some((part) => part === "." || part === "..")) {
        issues.push({ path: [...base, "dst"], message: "Repository destination must be a safe non-empty relative path.", severity: "error" });
      } else {
        const normalized = trimmed.replace(/\/{2,}/g, "/");
        const overlap = destinations.find(({ dst: other }) => normalized === other || normalized.startsWith(`${other}/`) || other.startsWith(`${normalized}/`));
        if (overlap) issues.push({ path: [...base, "dst"], message: `Repository destination overlaps config item ${overlap.index + 1}.`, severity: "error" });
        destinations.push({ dst: normalized, index });
      }
    }
    const checkGpg = repo.check_gpg ?? true;
    const keyrings = asList(repo.keyring);
    if (keyrings.some((item) => typeof item !== "string" || !item.startsWith("/") || /[\s$#,\[\]=\u0000-\u001f\u007f]/.test(item))) {
      issues.push({ path: [...base, "keyring"], message: "Keyrings must be absolute paths without whitespace or native delimiters.", severity: "error" });
    }
    if (checkGpg && keyrings.filter(Boolean).length === 0) {
      issues.push({ path: [...base, "keyring"], message: "At least one keyring is required when check_gpg is enabled.", severity: "error" });
    }
    const distributions = asList(repo.dist);
    if (repo.dist !== undefined && !validAptSelection(repo.dist, /^[A-Za-z0-9][A-Za-z0-9._+-]*$/, true)) {
      issues.push({ path: [...base, "dist"], message: "Distribution entries contain unsupported characters.", severity: "error" });
    }
    if (repo.section !== undefined && !validAptSelection(repo.section, /^[A-Za-z0-9][A-Za-z0-9._+/-]*$/)) {
      issues.push({ path: [...base, "section"], message: "Section entries contain unsupported characters.", severity: "error" });
    }
    if (repo.arch !== undefined && !validAptSelection(repo.arch, /^[A-Za-z0-9][A-Za-z0-9._+-]*$/)) {
      issues.push({ path: [...base, "arch"], message: "Architecture entries contain unsupported characters.", severity: "error" });
    }
    if (distributions.some((item) => typeof item === "string" && item.endsWith("/"))) {
      if (distributions.some((item) => typeof item === "string" && !item.endsWith("/"))) issues.push({ path: [...base, "dist"], message: "Flat and regular distributions cannot be mixed.", severity: "error" });
      if (repo.section !== undefined) issues.push({ path: [...base, "section"], message: "Flat repositories do not accept section.", severity: "error" });
      if (repo.arch !== undefined) issues.push({ path: [...base, "arch"], message: "Flat repositories do not accept arch.", severity: "error" });
    }
    if (asList(repo.arch).includes("source")) {
      issues.push({ path: [...base, "arch"], message: "Use source=true instead of the 'source' architecture.", severity: "error" });
    }
  });
}

export function validateConfig(value) {
  const issues = [];
  checkSchema(value, configSchema, [], issues);
  if (!isObject(value)) return issues;

  if (Array.isArray(value.settings?.plugins)) {
    issues.push({ path: ["settings", "plugins"], message: "The legacy plugin path list is deprecated; use the enable-only plugin map.", severity: "warning" });
  }
  if (isObject(value.settings) && Object.hasOwn(value.settings, "max_runtime") && !validDuration(value.settings.max_runtime)) {
    issues.push({ path: ["settings", "max_runtime"], message: "Use an ISO 8601 duration, PUSH, or an empty string.", severity: "error" });
  }

  if (!isObject(value.packages)) return issues;
  for (const [key, packageValue] of Object.entries(value.packages)) {
    const packagePath = ["packages", key];
    if (!isObject(packageValue)) continue;
    if (packageValue.id !== undefined && packageValue.id !== key) {
      issues.push({ path: [...packagePath, "id"], message: `Package ID must match its map key (${key}).`, severity: "error" });
    }
    if (key.startsWith("_") || RESERVED_PACKAGE_IDS.has(key)) {
      issues.push({ path: packagePath, message: "Package ID collides with a reserved Packages attribute.", severity: "error" });
    }
    if (Object.hasOwn(packageValue, "syncrate") && !validDuration(packageValue.syncrate)) {
      issues.push({ path: [...packagePath, "syncrate"], message: "Use an ISO 8601 day/hour/minute/second duration, PUSH, or an empty string.", severity: "error" });
    }
    const method = packageValue.synctype;
    if (typeof method === "string" && !BUILTIN_METHODS.includes(method)) {
      issues.push({ path: [...packagePath, "synctype"], message: `Unknown method '${method}' is preserved for an external plugin.`, severity: "warning" });
      continue;
    }
    const options = packageValue.settings?.options;
    const methodSchema = typeof method === "string" && Object.hasOwn(methodSchemas, method) ? methodSchemas[method] : null;
    if (isObject(options) && methodSchema) {
      for (const option of Object.keys(options)) {
        if (!Object.hasOwn(methodSchema.properties || {}, option) && method !== "apt-mirror2") {
          issues.push({ path: [...packagePath, "settings", "options", option], message: `Unknown ${method} option is preserved.`, severity: "warning" });
        }
      }
    }
    if (method === "ftpsync" && typeof packageValue.settings?.src === "string" && !packageValue.settings.src.startsWith("rsync://") && !options?.path) {
      issues.push({ path: [...packagePath, "settings", "options", "path"], message: "path is required when ftpsync src is a bare hostname.", severity: "error" });
    }
    if (method === "apt-mirror2") validateAptMirror(packageValue, packagePath, issues);
  }
  return issues;
}
