import test from 'node:test';
import assert from 'node:assert/strict';
import { authUrl, speedRequest, runProvider } from '../seventeence-provider.mjs';

const profile = {test_type:'HTTP',operators:[1,2,7],area:1,provinces:[195],cities:[],node_type:1,nodes_per_region:1};
const job = {id:'j1',target:'https://example.test/',profile};
class Socket extends EventTarget {
  constructor(url) { super(); this.url=url; this.readyState=1; this.sent=[]; queueMicrotask(()=>this.emit({rt:1})); }
  emit(x) { this.dispatchEvent(new MessageEvent('message',{data:JSON.stringify(x)})); }
  send(data) { this.sent.push(JSON.parse(data)); this.onSend?.(); }
  close() { this.readyState=3; }
}
test('auth vector and request selection', () => {
  const url=new URL(authUrl(' user@example.test ','password',1700000000));
  assert.equal(url.origin,'wss://wsapi.17ce.com:8001'); assert.equal(url.pathname,'/socket');
  assert.equal(url.searchParams.get('code'),'807e198189f750a21408bf8d0678e348');
  assert.equal(url.searchParams.get('user'),'user@example.test');
  assert.deepEqual(speedRequest(job),{txnid:1,nodetype:[1],num:1,Url:job.target,TestType:'HTTP',TimeOut:20,Request:'GET',NoCache:true,type:1,isps:[1,2,7],areas:[1],pro_ids:'195'});
});
test('auth, permit, single submission, deduplicate and aggregate official frames', async () => {
 let socket, begin=0, accepted=[];
 const result=await runProvider(job,{username:'u',api_token:'p'},{socketFactory:url=>{socket=new Socket(url);socket.onSend=()=>queueMicrotask(()=>{socket.emit({rt:1,txnid:1,type:'TaskAccept',data:{TaskId:'tid'}});socket.emit({rt:1,txnid:1,type:'NewData',data:{NodeID:1,HttpCode:200,TotalTime:1.5,NodeInfo:{isp:1},HttpHead:'private'}});socket.emit({rt:1,txnid:1,type:'NewData',data:{NodeID:1,HttpCode:500,NodeInfo:{isp:1}}});socket.emit({rt:1,txnid:1,type:'TaskEnd',data:{}})});return socket},begin:async()=>{begin++;return true},accepted:async r=>accepted.push(r),timeoutMs:1000});
 assert.equal(begin,1); assert.equal(socket.sent.length,1); assert.equal(accepted[0].task_id,'tid');
 assert.equal(result.status,'complete'); assert.equal(result.node_count,1); assert.equal(result.success_count,1); assert.equal(result.operators['1'].avg_seconds,1.5); assert.ok(!JSON.stringify(result).includes('private'));
});
test('no permit and post-send disconnect never resend',async()=>{
 let socket; const opts={socketFactory:url=>{socket=new Socket(url);return socket},begin:async()=>false,accepted:async()=>{},timeoutMs:1000};
 assert.equal((await runProvider(job,{username:'u',api_token:'p'},opts)).status,'blocked'); assert.equal(socket.sent.length,0);
 opts.begin=async()=>true; opts.socketFactory=url=>{socket=new Socket(url);socket.onSend=()=>queueMicrotask(()=>socket.dispatchEvent(new Event('close')));return socket};
 assert.equal((await runProvider(job,{username:'u',api_token:'p'},opts)).status,'uncertain'); assert.equal(socket.sent.length,1);
});


test('begin uncertainty and authentication failure do not submit',async()=>{
 let socket;
 let result=await runProvider(job,{username:'u',api_token:'p'},{socketFactory:url=>{socket=new Socket(url);return socket},begin:async()=>{throw Error('secret')},accepted:async()=>{},timeoutMs:1000});
 assert.equal(result.status,'failed');assert.equal(result.error_code,'begin_unavailable');assert.equal(socket.sent.length,0);
 result=await runProvider(job,{username:'u',api_token:'p'},{socketFactory:url=>{socket=new Socket(url);socket.emit=()=>socket.dispatchEvent(new MessageEvent('message',{data:JSON.stringify({rt:10008,error:'private'})}));return socket},begin:async()=>true,accepted:async()=>{},timeoutMs:1000});
 assert.equal(result.status,'blocked');assert.equal(result.error_code,'provider_auth_failed');assert.equal(socket.sent.length,0);
});

test('empty terminal is a failure and does not invent timings',async()=>{
 let socket;
 const result=await runProvider(job,{username:'u',api_token:'p'},{socketFactory:url=>{socket=new Socket(url);socket.onSend=()=>queueMicrotask(()=>socket.emit({rt:1,txnid:1,type:'TaskEnd',data:{}}));return socket},begin:async()=>true,accepted:async()=>{},timeoutMs:1000});
 assert.equal(result.status,'failed');assert.equal(result.error_code,'no_results');assert.equal(result.node_count,0);
});

test('error response never becomes a successful terminal',async()=>{
 let socket;
 const result=await runProvider(job,{username:'u',api_token:'p'},{socketFactory:url=>{socket=new Socket(url);socket.onSend=()=>queueMicrotask(()=>{socket.emit({rt:1,txnid:1,type:'NewData',data:{NodeID:1,HttpCode:200}});socket.emit({rt:10013,txnid:1,type:'TaskEnd',data:{}})});return socket},begin:async()=>true,accepted:async()=>{},timeoutMs:1000});
 assert.equal(result.status,'failed');assert.equal(result.error_code,'provider_task_failed');
});

test('slow accepted callback cannot resend after provider timeout',async()=>{
 let socket, finishAccepted;
 const gate=new Promise(r=>{finishAccepted=r});
 const result=await runProvider(job,{username:'u',api_token:'p'},{socketFactory:url=>{socket=new Socket(url);socket.onSend=()=>queueMicrotask(()=>socket.emit({rt:1,txnid:1,type:'TaskAccept',data:{TaskId:'tid'}}));return socket},begin:async()=>true,accepted:async()=>gate,timeoutMs:20});
 assert.equal(result.status,'uncertain');assert.equal(result.error_code,'provider_timeout');assert.equal(socket.sent.length,1);finishAccepted();await new Promise(r=>setTimeout(r,5));assert.equal(socket.sent.length,1);
});
