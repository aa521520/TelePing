import {test} from 'node:test';
import assert from 'node:assert/strict';
import {acceptMenu, openLocalMenu, relayWakeAt, flushMenuRelay, openMenuRequest} from '../shared-menu.mjs';

const event = {chat_id:'-100',message_id:73,user_id:5};
const update = {update_id:19,message:{message_id:73,text:'/menu',chat:{id:-100,type:'supergroup'},from:{id:5}}};
const initial = () => ({chat_id:'-100',inbox:[],targets:['http://example.test/'],jobs:[]});
function fixture() {
  let value=initial();
  const service={lock:Promise.resolve(),env:{},async save(s){value=structuredClone(s)}};
  const load=async()=>structuredClone(value);
  return {service,load};
}

test('one native menu event is deduplicated across Telegram delivery and relay',()=>{
  const s=initial();
  assert.deepEqual(openLocalMenu(s,update),{handled:true,show:true});
  assert.equal(s.menu_relays.length,1);
  assert.deepEqual(s.menu_relays[0].event,event);
  assert.deepEqual(openLocalMenu(s,{...update,update_id:999}),{handled:true,show:false});
  assert.deepEqual(acceptMenu(s,event),{duplicate:true});
  assert.equal(s.menu_relays.length,1);
  assert.deepEqual(s.targets,['http://example.test/']);
  assert.deepEqual(s.jobs,[]);
});

test('received menu is durable, deduplicated and never forwarded back',()=>{
  const s=initial();
  assert.deepEqual(acceptMenu(s,{...event,text:'/delete',token:'NEVER_COPY'}),{duplicate:false});
  assert.deepEqual(acceptMenu(s,event),{duplicate:true});
  assert.equal(s.inbox.length,1);
  assert.equal(s.inbox[0].message.text,'/menu');
  assert.ok(!JSON.stringify(s).includes('NEVER_COPY'));
  assert.deepEqual(openLocalMenu(s,s.inbox[0]),{handled:true,show:true});
  assert.equal(s.menu_relays?.length||0,0);
  assert.deepEqual(openLocalMenu(s,update),{handled:true,show:false});
});

test('sharing rejects other commands, private chats, bots and invalid identities',()=>{
  const s=initial();
  for(const text of ['/menu@otherbot','/menu anything','/check','/delete x.test']) {
    assert.equal(openLocalMenu(s,{...update,message:{...update.message,text}}).handled,false);
  }
  assert.equal(openLocalMenu(s,{...update,message:{...update.message,chat:{id:5,type:'private'}}}).handled,false);
  assert.equal(openLocalMenu(s,{...update,message:{...update.message,from:{id:5,is_bot:true}}}).handled,false);
  assert.equal(openLocalMenu(s,{...update,message:{...update.message,sender_chat:{id:-100}}}).handled,false);
  for(const bad of [{...event,chat_id:'-200'},{...event,message_id:0},{...event,user_id:-1},{...event,chat_id:'-1e2'}])assert.equal(acceptMenu(s,bad),null);
  assert.equal(s.inbox.length,0);
});

test('menu queue overflow fails before marking a new message processed',()=>{
  const s=initial();s.inbox=Array.from({length:100},()=>({}));
  assert.throws(()=>acceptMenu(s,event),/menu_inbox_full/);
  s.inbox=[];
  s.menu_relays=Array.from({length:100},(_,i)=>({event:{...event,message_id:i+100}}));
  assert.throws(()=>openLocalMenu(s,update),/menu_relay_full/);
  assert.equal(s.menu_seen?.length||0,0);
});

test('relay releases local lock during RPC and preserves concurrent edits',async()=>{
  const {service,load}=fixture();let s=await load();openLocalMenu(s,update);await service.save(s);
  service.env.PEER_MENU={async openMenu(received){
    assert.deepEqual(received,event);
    let timer;
    await Promise.race([service.lock,new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('local lock held during RPC')),100))]);
    clearTimeout(timer);
    const current=await load();current.targets.push('http://concurrent.test/');await service.save(current);
    return {ok:true};
  }};
  assert.ok(Number.isFinite(relayWakeAt(await load())));
  await flushMenuRelay(service,load);
  s=await load();assert.equal(s.menu_relays.length,0);assert.equal(s.targets.length,2);
});

test('lost RPC acknowledgment retries the same menu identity without a second request',async()=>{
  const {service,load}=fixture(),receiver=initial();let attempts=0;
  const oldNow=Date.now;let now=oldNow();Date.now=()=>now;
  try {
    const s=await load();openLocalMenu(s,update);await service.save(s);
    service.env.PEER_MENU={async openMenu(e){attempts++;acceptMenu(receiver,e);if(attempts===1)throw Error('lost acknowledgment');return {ok:true}}};
    await flushMenuRelay(service,load);
    assert.equal((await load()).menu_relays.length,1);assert.equal(receiver.inbox.length,1);
    now=relayWakeAt(await load())+1;
    await flushMenuRelay(service,load);
    assert.equal((await load()).menu_relays.length,0);assert.equal(receiver.inbox.length,1);assert.equal(attempts,2);
  } finally {Date.now=oldNow}
});

test('RPC boundary forwards only a menu envelope through the local Durable Object',async()=>{
  let calls=0;
  const env={RUNNER_API_TOKEN:'LOCAL_TEST_ONLY',STATE:{idFromName(){return 'main'},get(){return {async fetch(request){
    calls++;assert.equal(new URL(request.url).pathname,'/internal/menu');
    assert.equal(request.headers.get('authorization'),'Bearer LOCAL_TEST_ONLY');
    assert.deepEqual(await request.json(),event);
    return Response.json({ok:true,secret:'NEVER_RETURN'});
  }}}}};
  assert.deepEqual(await openMenuRequest(env,{...event,extra:'discard'}),{ok:true});
  assert.deepEqual(await openMenuRequest(env,{...event,user_id:0}),{ok:false});
  assert.equal(calls,1);
});
