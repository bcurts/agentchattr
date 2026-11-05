'use strict';
// Parses configuration as data only. !!js expressions are represented by an
// inert marker and refused if they appear in the selected model configuration.
const fs = require('fs');
const path = require('path');
try {
  const request = JSON.parse(fs.readFileSync(0, 'utf8'));
  const yaml = require(require.resolve('js-yaml', { paths: [path.dirname(request.entry)] }));
  const expression = new yaml.Type('tag:yaml.org,2002:js', { kind: 'scalar', construct: () => ({__dsh_expression__: true}) });
  const schema = yaml.DEFAULT_SCHEMA.extend([expression]);
  const load = p => yaml.load(fs.readFileSync(p, 'utf8'), {schema});
  const rows = load(request.webPatch);
  if (!Array.isArray(rows)) throw new Error('invalid_profile');
  const getRow = id => rows.find(row => row && row.id === id);
  const defaults = getRow('agent-default-model')?.config || {};
  const provider = request.provider || defaults.provider;
  const model = request.model || defaults.model;
  if (typeof provider !== 'string' || !provider || typeof model !== 'string' || !model) throw new Error('missing_selection');
  const patches = [];
  let keyRef;
  if (provider !== 'deepseek-official') {
    const source = getRow('llm-pi-ai')?.config?.providers?.[provider];
    if (!source || typeof source !== 'object') throw new Error('unknown_provider');
    if (!Array.isArray(source.models) || !source.models.some(item => item?.id === model)) throw new Error('unknown_model');
    keyRef = source.apiKeyEnv;
    const config = {providers: {[provider]: source}};
    if (JSON.stringify(config).includes('__dsh_expression__')) throw new Error('unsupported_expression');
    patches.push({id:'llm-pi-ai',config});
  } else {
    const config = getRow('llm-deepseek')?.config;
    if (config) {
      if (JSON.stringify(config).includes('__dsh_expression__')) throw new Error('unsupported_expression');
      if (Array.isArray(config.models) && !config.models.some(item => item?.id === model)) throw new Error('unknown_model');
      patches.push({id:'llm-deepseek', config});
    }
    keyRef = 'DEEPSEEK_API_KEY';
  }
  const refs = {}, records = {};
  const credentials = fs.existsSync(request.credentials) ? load(request.credentials) : {};
  if (keyRef && credentials?.refs?.[keyRef]) refs[keyRef] = credentials.refs[keyRef];
  const recordName = `llm-pi-ai/${provider}`;
  if (credentials?.records?.[recordName]) records[recordName] = credentials.records[recordName];
  const envHasKey = keyRef && !!process.env[keyRef];
  if (keyRef && !refs[keyRef] && !envHasKey) throw new Error('missing_credential');
  if (!keyRef && !records[recordName]) throw new Error('missing_credential');
  const outputCredentials = {version: 1};
  if (Object.keys(refs).length) outputCredentials.refs = refs;
  if (Object.keys(records).length) outputCredentials.records = records;
  process.stdout.write(JSON.stringify({provider, model, patches, credentials: outputCredentials}));
} catch (error) {
  const known = new Set(['invalid_profile','missing_selection','unknown_provider','unknown_model','unsupported_expression','missing_credential']);
  process.stderr.write(known.has(error.message) ? error.message : 'parse_or_read_error');
  process.exitCode = 1;
}
