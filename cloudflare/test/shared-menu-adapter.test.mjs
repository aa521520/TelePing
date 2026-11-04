import {test} from 'node:test';
import assert from 'node:assert/strict';
import worker,{TelePing} from '../worker.mjs';
class Storage {data=new Map();async get(k){return this.data.get(k)}async put(k,v){this.data.set(k,v)}async setAlarm(v){this.alarm=v}}
const env={STATE_ENCRYPTION_KEY:'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',RUNNER_API_TOKEN:'runner',WEBHOOK_SECRET:'hook',TELEGRAM_BOT_TOKEN:'test',TELEGRAM_CHAT_ID:'-100',GITHUB_REPOSITORY:'owner/repo',GITHUB_DISPATCH_TOKEN:'test'};
const update=(update_id,message_id,text='/menu',user=5)=>({update_id,message:{message_id,chat:{id:-100,type:'supergroup'},from:{id:user,is_bot:false},text}});
const post=(o,u)=>o.fetch(new Request('https://service/telegram',{method:'POST',headers:{'X-Telegram-Bot-Api-Secret-Token':'hook'},body:JSON.stringify(u)}));

test('bare menu wakes peer once even when Telegram delivers twice',async()=>{
 let relays=[];const storage=new Storage(),obj=new TelePing({storage},{...env,PEER_MENU:{openMenu:async e=>{relays.push(e);return true}}});
 const old=globalThis.fetch;globalThis.fetch=async(url)=>String(url).endsWith('getChatAdministrators')?Response.json({ok:true,result:[{status:'administrator',user:{id:5,is_bot:false}}]}):Response.json({ok:true,result:{message_id:99}});
 try{await post(obj,update(1,71));await obj.alarm();await post(obj,update(2,71));await obj.alarm();let s=await obj.get();assert.equal(relays.length,1);assert.ok(s.outbox.length<=1);assert.equal(s.targets.length,0)}finally{globalThis.fetch=old}
});

test('internal route requires auth and is absent on public endpoint',async()=>{
 let obj=new TelePing({storage:new Storage()},env),req=()=>new Request('https://service/internal/menu',{method:'POST',headers:{authorization:'Bearer bad'},body:'{}'});
 assert.equal((await obj.fetch(req())).status,401);assert.equal((await worker.fetch(req(),env)).status,404);
});

test('internal relay enqueues only valid bound-group menu and never reflects it back',async()=>{
 let relays=[],storage=new Storage(),obj=new TelePing({storage},{...env,PEER_MENU:{openMenu:async e=>{relays.push(e);return true}}});
 const old=globalThis.fetch;globalThis.fetch=async(url)=>String(url).endsWith('getChatAdministrators')?Response.json({ok:true,result:[{status:'creator',user:{id:5,is_bot:false}}]}):Response.json({ok:true,result:{message_id:99}});
 const call=(body,token='runner')=>obj.fetch(new Request('https://service/internal/menu',{method:'POST',headers:{authorization:'Bearer '+token},body:JSON.stringify(body)}));
 try{
  assert.equal((await call({chat_id:'-200',message_id:50,user_id:5})).status,400);
  assert.equal((await call({chat_id:'-100',message_id:50,user_id:5},'bad')).status,401);
  assert.equal((await call({chat_id:'-100',message_id:50,user_id:5,extra:'private'})).status,200);
  let s=await obj.get();assert.equal(s.inbox.length,1);assert.equal(JSON.stringify(s.inbox).includes('private'),false);
  assert.equal((await call({chat_id:'-100',message_id:50,user_id:5})).status,200);
  s=await obj.get();assert.equal(s.inbox.length,1);
  await obj.alarm();assert.equal(relays.length,0);s=await obj.get();assert.equal(s.inbox.length,0);
 }finally{globalThis.fetch=old}
});

test('non-admin menu never creates peer wake',async()=>{
 let relays=[],obj=new TelePing({storage:new Storage()},{...env,PEER_MENU:{openMenu:async e=>{relays.push(e);return true}}});
 const old=globalThis.fetch;globalThis.fetch=async()=>Response.json({ok:true,result:[]});
 try{await post(obj,update(3,72));await obj.alarm();assert.equal(relays.length,0);assert.equal((await obj.get()).outbox.length,0)}finally{globalThis.fetch=old}
});

test('relay rechecks current admin permissions before showing menu',async()=>{
 let storage=new Storage(),obj=new TelePing({storage},env),old=globalThis.fetch;globalThis.fetch=async()=>Response.json({ok:true,result:[]});
 try{let r=await obj.fetch(new Request('https://service/internal/menu',{method:'POST',headers:{authorization:'Bearer runner'},body:JSON.stringify({chat_id:'-100',message_id:99,user_id:5})}));assert.equal(r.status,200);await obj.alarm();let s=await obj.get();assert.equal(s.inbox.length,0);assert.equal(s.outbox.length,0)}finally{globalThis.fetch=old}
});
