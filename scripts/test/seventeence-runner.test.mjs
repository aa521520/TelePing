import test from 'node:test';
import assert from 'node:assert/strict';
import { encrypt, decrypt } from '../../cloudflare/core.mjs';
import { Service, runClaim } from '../seventeence-runner.mjs';
const key=Buffer.alloc(32,7).toString('base64url');
const plan={run_id:'123-1',lease_token:'lease',credentials:{username:'u',api_token:'secret'},jobs:[{id:'j',target:'https://example.test/',profile:{}}]};
test('service authenticates fixed API and retries same terminal event without provider resubmit',async()=>{
 let requests=[], events=0, calls=0;
 const fetcher=async(url,opts)=>{
  assert.equal(opts.headers.Authorization,'Bearer runner-secret'); const body=JSON.parse(opts.body); requests.push(new URL(url).pathname);
  if(url.endsWith('/api/claim'))return Response.json({should_check:true,payload:await encrypt({...plan,executor_id:body.executor_id},key,'17ce:plan:123-1')});
  if(url.endsWith('/api/event')) {let e=await decrypt(body.payload,key,'17ce:event:123-1');assert.equal(e.event_id,'j:terminal');assert.match(e.executor_id,/^[0-9a-f-]{36}$/);events++;if(events===1)throw Error('secret-canary');}
  return Response.json({ok:true});
 };
 const service=new Service('https://service.example','runner-secret',key,fetcher);
 const count=await runClaim(service,'123-1',{provider:async()=>{calls++;return {status:'complete',node_count:1,success_count:1,failure_count:0,operators:{},begun:true}}});
 assert.equal(count,1);assert.equal(events,2);assert.equal(calls,1);assert.equal(requests.at(-1),'/api/finish');
});
test('setup and invalid endpoints use fixed errors',async()=>{
 const service=new Service('https://service.example','secret',key,async()=>Response.json({ok:true}));
 await service.setup();
 assert.throws(()=>new Service('http://service.example','secret',key),/configuration_invalid/);
 const failing=new Service('https://service.example','secret',key,async()=>{throw Error('credential-canary')});
 await assert.rejects(()=>failing.setup(),/^Error: service_unavailable$/);
});

test('authentication failure stops batch and still finalizes claim',async()=>{
 let providerCalls=0, terminal=[];const p={...plan,jobs:[{id:'j'},{id:'j2'}]};
 const service={claim:async()=>p,event:async(_p,_j,_kind,result)=>terminal.push(result),finish:async()=>terminal.push('finished')};
 assert.equal(await runClaim(service,'123-1',{provider:async()=>{providerCalls++;return {status:'blocked',error_code:'provider_auth_failed',node_count:0,success_count:0,failure_count:0,operators:{},begun:false}}}),1);
 assert.equal(providerCalls,1);assert.equal(terminal[0].error_code,'provider_auth_failed');assert.equal(terminal[1],'finished');
});

test('concurrent executors cannot finish another executor lease',async()=>{
 let owner, finishers=[], release, started;
 const ready=new Promise(r=>{started=r}); const gate=new Promise(r=>{release=r});
 const service={claim:async(_run,executor)=>{if(owner)return null;owner=executor;return {...plan,executor_id:executor}},event:async()=>{},finish:async(p)=>finishers.push(p.executor_id)};
 const provider=async()=>{started();await gate;return {status:'complete',node_count:0,success_count:0,failure_count:0,operators:{}}};
 const first=runClaim(service,'123-1',{provider}); await ready;
 if (typeof owner !== 'string') { release(); await first; assert.fail('executor id missing'); }
 assert.equal(await runClaim(service,'123-1',{provider}),0);
 assert.deepEqual(finishers,[]);release();assert.equal(await first,1);
 assert.equal(typeof owner,'string');assert.match(owner,/^[0-9a-f-]{36}$/);assert.deepEqual(finishers,[owner]);
});

test('claim rejects a plan owned by a different executor',async()=>{
 const service=new Service('https://service.example','secret',key,async()=>Response.json({should_check:true,payload:await encrypt({...plan,executor_id:'wrong'},key,'17ce:plan:123-1')}));
 await assert.rejects(()=>service.claim('123-1','11111111-1111-4111-8111-111111111111'),/^Error: service_invalid$/);
});

test('missing executor identifier is rejected before any service call',async()=>{
 let called=false;
 const service=new Service('https://service.example','secret',key,async()=>{called=true;return Response.json({should_check:false})});
 await assert.rejects(()=>service.claim('123-1'),/^Error: configuration_invalid$/);assert.equal(called,false);
});
