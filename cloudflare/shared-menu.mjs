export const SHARED_MENU_PATH='/internal/menu';
const MAX_SEEN=5000, MAX_PENDING=100, RELAY_TTL=600000;

function envelope(value) {
  if(!value||typeof value!=='object'||Array.isArray(value))return null;
  const chat=String(value.chat_id);
  if(!/^-[1-9][0-9]*$/.test(chat)||!Number.isSafeInteger(Number(chat)))return null;
  if(!Number.isSafeInteger(value.message_id)||value.message_id<=0||!Number.isSafeInteger(value.user_id)||value.user_id<=0)return null;
  return {chat_id:chat,message_id:value.message_id,user_id:value.user_id};
}
function key(event){return event.chat_id+':'+event.message_id}
function eventFrom(update) {
  const m=update?.message;
  if(update?.callback_query||!m||!['group','supergroup'].includes(m.chat?.type)||m.sender_chat||m.from?.is_bot||typeof m.text!=='string'||!/^\/menu$/i.test(m.text.trim()))return null;
  return envelope({chat_id:m.chat.id,message_id:m.message_id,user_id:m.from?.id});
}
function bound(s,event){return event&&event.chat_id===String(s.chat_id)}

export function acceptMenu(s,value) {
  const event=envelope(value);
  if(!bound(s,event))return null;
  const identity=key(event);
  if((s.menu_seen||[]).includes(identity)||(s.inbox||[]).some(u=>{const e=eventFrom(u);return e&&key(e)===identity}))return {duplicate:true};
  if(s.inbox.length>=MAX_PENDING)throw Error('menu_inbox_full');
  s.inbox.push({_shared_menu:true,message:{message_id:event.message_id,text:'/menu',chat:{id:Number(event.chat_id),type:'supergroup'},from:{id:event.user_id,is_bot:false}}});
  return {duplicate:false};
}

// Call only after the receiving bot has rechecked the current group administrators.
export function openLocalMenu(s,update) {
  const event=eventFrom(update);
  if(!bound(s,event))return {handled:false,show:false};
  const identity=key(event);
  if((s.menu_seen||[]).includes(identity))return {handled:true,show:false};
  const forward=update._shared_menu!==true;
  if(forward&&(s.menu_relays||[]).length>=MAX_PENDING)throw Error('menu_relay_full');
  s.menu_seen=[...(s.menu_seen||[]),identity].slice(-MAX_SEEN);
  if(forward)(s.menu_relays||=[]).push({key:identity,event,created:Date.now(),at:Date.now(),attempt:0});
  return {handled:true,show:true};
}

export function relayWakeAt(s) {
  const times=(s.menu_relays||[]).map(x=>x.at).filter(Number.isFinite);
  return times.length?Math.min(...times):null;
}

async function locked(service,fn) {
  const previous=service.lock;let release;
  service.lock=new Promise(resolve=>release=resolve);
  await previous;
  try{return await fn()}finally{release()}
}

// Load again after the RPC: menu requests must never overwrite concurrent CRUD or results.
// The RPC runs outside the application lock so simultaneous two-way calls cannot deadlock.
export async function flushMenuRelay(service,load) {
  const item=await locked(service,async()=>{
    const s=await load(),before=(s.menu_relays||[]).length,now=Date.now();
    s.menu_relays=(s.menu_relays||[]).filter(x=>bound(s,envelope(x.event))&&Number.isFinite(x.created)&&now-x.created<RELAY_TTL);
    if(s.menu_relays.length!==before)await service.save(s);
    return s.menu_relays.find(x=>x.at<=now);
  });
  if(!item)return;
  let ok=false,timer;
  try {
    if(!service.env.PEER_MENU)throw Error('menu_peer_unavailable');
    const response=await Promise.race([
      service.env.PEER_MENU.openMenu(item.event),
      new Promise((_,reject)=>timer=setTimeout(()=>reject(Error('menu_peer_timeout')),5000)),
    ]);
    ok=response?.ok===true;
  } catch {} finally {clearTimeout(timer)}
  await locked(service,async()=>{
    const s=await load(),pending=(s.menu_relays||[]).find(x=>x.key===item.key);
    if(!pending)return;
    if(ok)s.menu_relays=s.menu_relays.filter(x=>x.key!==item.key);
    else {pending.at=Date.now()+Math.min(60000,1000*2**Math.min(pending.attempt||0,6));pending.attempt=(pending.attempt||0)+1}
    await service.save(s);
  });
}

// Named WorkerEntrypoint capability; no public HTTP route forwards to this endpoint.
export async function openMenuRequest(env,value) {
  const event=envelope(value);
  if(!event||!env.RUNNER_API_TOKEN)return {ok:false};
  try {
    const object=env.STATE.get(env.STATE.idFromName('main'));
    const response=await object.fetch(new Request('https://menu.internal'+SHARED_MENU_PATH,{method:'POST',headers:{authorization:'Bearer '+env.RUNNER_API_TOKEN,'content-type':'application/json'},body:JSON.stringify(event)}));
    if(!response.ok)return {ok:false};
    const result=await response.json();
    return {ok:result.ok===true};
  } catch {return {ok:false}}
}
