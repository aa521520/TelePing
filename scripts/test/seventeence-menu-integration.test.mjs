import {test} from 'node:test';
import assert from 'node:assert/strict';
import {TelePing} from '../../cloudflare/worker.mjs';
import {SeventeenCE} from '../../cloudflare17ce/worker.mjs';
import {openMenuRequest} from '../../cloudflare/shared-menu.mjs';

class Storage {
  data=new Map();
  async get(k){return this.data.get(k)}
  async put(k,v){this.data.set(k,v)}
  async setAlarm(at){this.alarm=at}
}
function fixture(){
  const base={TELEGRAM_CHAT_ID:'-100',STATE_ENCRYPTION_KEY:'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',RUNNER_API_TOKEN:'test-runner',WEBHOOK_SECRET:'test-hook',BOT_USERNAME:'test_bot'};
  const aenv={...base,TELEGRAM_BOT_TOKEN:'test-original'},benv={...base,TELEGRAM_BOT_TOKEN:'test-17ce'};
  const a=new TelePing({storage:new Storage()},aenv),b=new SeventeenCE({storage:new Storage()},benv);
  aenv.STATE={idFromName:()=> 'main',get:()=>a};benv.STATE={idFromName:()=> 'main',get:()=>b};
  aenv.PEER_MENU={openMenu:e=>openMenuRequest(benv,e)};benv.PEER_MENU={openMenu:e=>openMenuRequest(aenv,e)};
  return {a,b};
}
function receive(object,updateId,messageId=321,user=5){return object.fetch(new Request('https://example.invalid/telegram',{method:'POST',headers:{'X-Telegram-Bot-Api-Secret-Token':'test-hook'},body:JSON.stringify({update_id:updateId,message:{message_id:messageId,chat:{id:-100,type:'supergroup'},from:{id:user},text:'/menu'}})}))}
async function runScenario(delivery){
  const {a,b}=fixture(),sent=[],previous=globalThis.fetch;
  globalThis.fetch=async(url,options)=>{
    const method=String(url).split('/').pop(),body=JSON.parse(options.body);
    if(method==='getChatAdministrators')return Response.json({ok:true,result:[{status:'creator',user:{id:5}}]});
    if(method==='sendMessage'){sent.push({bot:String(url).includes('test-original')?'original':'17ce',...body});return Response.json({ok:true,result:{message_id:99}})}
    throw Error('Unexpected external call during menu opening');
  };
  try {
    const s=await a.get();s.last_check_started=Date.now()/1000;s.last_dispatch=Date.now();await a.save(s);
    if(delivery.includes('a'))assert.equal((await receive(a,1)).status,200);
    if(delivery.includes('b'))assert.equal((await receive(b,901)).status,200);
    await Promise.all([a.alarm(),b.alarm()]);
    await Promise.all([a.alarm(),b.alarm()]);
    assert.equal(sent.length,2);
    assert.deepEqual(sent.map(x=>x.bot).sort(),['17ce','original']);
    assert.ok(sent.every(x=>x.reply_markup?.inline_keyboard?.length));
    const sa=await a.get(),sb=await b.load();
    assert.equal(sa.requests.length,0);assert.equal(sb.jobs.length,0);
    assert.equal((sa.menu_relays?.length||0)+(sb.menu_relays?.length||0),0);
    assert.equal(sa.inbox.length+sb.inbox.length,0);
  } finally {globalThis.fetch=previous}
}

test('only original bot receives native menu and both independent panels open',()=>runScenario('a'));
test('only 17CE bot receives native menu and both independent panels open',()=>runScenario('b'));
test('simultaneous native delivery to both bots has no duplicate menus or lock cycle',()=>runScenario('ab'));
