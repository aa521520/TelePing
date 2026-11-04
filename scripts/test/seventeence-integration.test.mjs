import {test} from 'node:test';
import assert from 'node:assert/strict';
import {SeventeenCE} from '../../cloudflare17ce/worker.mjs';
import {Service, runClaim} from '../seventeence-runner.mjs';
import {runProvider} from '../seventeence-provider.mjs';

class Storage {
  data = new Map();
  async get(key) { return this.data.get(key); }
  async put(key, value) { this.data.set(key, value); }
  async setAlarm(value) { this.alarm = value; }
  async deleteAlarm() { this.alarm = undefined; }
}

function fixture({loseBegin = false} = {}) {
  const storage = new Storage();
  const env = {
    STATE_ENCRYPTION_KEY: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
    RUNNER_API_TOKEN: 'test-runner',
    WEBHOOK_SECRET: 'test-hook',
    TELEGRAM_BOT_TOKEN: 'test-bot',
    TELEGRAM_CHAT_ID: '-100',
    BOT_USERNAME: 'example_test_bot',
  };
  const object = new SeventeenCE({storage}, env);
  const service = new Service('https://controller.invalid', env.RUNNER_API_TOKEN,
    env.STATE_ENCRYPTION_KEY, async (url, options) => {
      const response = await object.fetch(new Request(url, options));
      if (loseBegin && new URL(url).pathname === '/api/begin') {
        throw Error('PRIVATE_TRANSPORT_DETAIL');
      }
      return response;
    });
  return {storage, object, service};
}

async function queueTemporary(object) {
  const state = await object.load();
  state.credentials = {username: 'test_user', api_token: 'PRIVATE_API_CANARY'};
  state.config_version = 1;
  state.jobs.push({
    id: 'test-job', target: 'http://example.test/', generation: 'temporary-generation',
    managed: false, round_id: 'test-round', profile: state.profile, status: 'queued',
  });
  await object.save(state);
}

const completed = {
  status: 'complete', task_id: 'task-1', node_count: 1, success_count: 1,
  failure_count: 0, allocated_node_count: 1, submitted: true,
  operators: {'1': {node_count: 1, success_count: 1, failure_count: 0,
    avg_seconds: 0.2, max_seconds: 0.2}},
};

test('unconfigured controller and real runner never reach paid provider', async () => {
  const {object, service} = fixture();
  assert.equal(await runClaim(service, '101-1', {
    provider() { throw Error('Provider must never be called'); },
  }), 0);
  assert.equal((await object.load()).history.length, 0);
});

test('encrypted runner/controller round is idempotent and preserves independent list', async () => {
  const {object, storage, service} = fixture();
  await queueTemporary(object);
  let submissions = 0;
  const provider = async (job, credentials, hooks) => {
    assert.equal(credentials.api_token, 'PRIVATE_API_CANARY');
    assert.equal(await hooks.begin(), true);
    submissions++;
    await hooks.accepted({task_id: 'task-1', allocated_node_count: 1, submitted: true});
    return completed;
  };
  assert.equal(await runClaim(service, '102-1', {provider}), 1);
  assert.equal(await runClaim(service, '102-1', {provider}), 0);
  const state = await object.load();
  assert.equal(submissions, 1);
  assert.equal(state.lease, null);
  assert.deepEqual(state.targets, []);
  assert.equal(state.history.at(-1).task_id, 'task-1');
  const reports = state.outbox.filter(message => message.text.includes('检测本轮汇总'));
  assert.equal(reports.length, 1);
  assert.match(reports[0].text, /正常：1/);
  assert.match(reports[0].text, /本轮费用参考估算：0\.01 元/);
  assert.ok(!state.outbox.some(message => /余额|累计费用/.test(message.text)));
  assert.ok(!reports[0].text.includes('http://example.test/'));
  assert.ok(!JSON.stringify(state.outbox).includes('PRIVATE_API_CANARY'));
  assert.ok(!JSON.stringify([...storage.data]).includes('PRIVATE_API_CANARY'));
});

test('official conflicting end counts remain uncertain through encrypted report delivery', async () => {
  const {object, service} = fixture();
  await queueTemporary(object);
  let sends = 0;
  class Socket {
    readyState = 1;
    listeners = {};
    addEventListener(type, callback) {
      this.listeners[type] = callback;
      if (type === 'message') queueMicrotask(() => callback({data: JSON.stringify({rt: 1})}));
    }
    emit(value) { this.listeners.message({data: JSON.stringify(value)}); }
    send(data) {
      sends++;
      const {txnid} = JSON.parse(data);
      queueMicrotask(() => {
        this.emit({rt: 1, txnid, type: 'TaskAccept', data: {'fixture-task:1': {
          txnid, TaskId: 'fixture-task', nodeCount: 1,
          nodes: {'101': {nodeinfo: {isp: '1'}}},
        }}});
        this.emit({rt: 1, txnid, type: 'NewData', data: {
          TaskId: 'fixture-task', NodeID: 101, HttpCode: 200, TotalTime: 0.2,
        }});
        this.emit({rt: 1, txnid, type: 'TaskEnd', data: {
          TaskId: 'fixture-task', TotalCount: 2, TotalCountOK: 1,
          TotalCountErr: 0, TotalCountTimeout: 1,
        }});
      });
    }
    close() { this.readyState = 3; }
  }
  const provider = (job, credentials, hooks) => runProvider(job, credentials,
    {...hooks, socketFactory: () => new Socket(), timeoutMs: 1000});
  assert.equal(await runClaim(service, '104-1', {provider}), 1);
  assert.equal(sends, 1);
  const state = await object.load();
  assert.equal(state.history.at(-1).status, 'uncertain');
  assert.equal(state.history.at(-1).error_code, 'provider_result_incomplete');
  const text = state.outbox.map(message => message.text).join('\n');
  assert.match(text, /正常：0/);
  assert.match(text, /费用待核实/);
  assert.match(text, /不完整估算/);
  assert.ok(!/余额|PRIVATE_API_CANARY/.test(text));
});

test('lost durable begin response cannot cause a provider submission', async () => {
  const {object, service} = fixture({loseBegin: true});
  await queueTemporary(object);
  let sends = 0;
  class Socket {
    readyState = 1;
    listeners = {};
    addEventListener(type, callback) {
      this.listeners[type] = callback;
      if (type === 'message') queueMicrotask(() => callback({data: JSON.stringify({rt: 1})}));
    }
    send() { sends++; }
    close() { this.readyState = 3; }
  }
  const provider = (job, credentials, hooks) => runProvider(job, credentials,
    {...hooks, socketFactory: () => new Socket(), timeoutMs: 1000});
  assert.equal(await runClaim(service, '103-1', {provider}), 1);
  assert.equal(sends, 0);
  const state = await object.load();
  assert.equal(state.history.at(-1).status, 'failed');
  assert.equal(state.history.at(-1).error_code, 'begin_unavailable');
});
